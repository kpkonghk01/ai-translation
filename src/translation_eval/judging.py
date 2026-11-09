"""judge：每個 ok 的 Run 由每個 Judge 評兩次（兩個獨立的請求，temperature 0）。兩個任務：
  mqm   每個要翻譯的 Component 的 Finding 與 Source defect；
  fact  每個含 Sensitive sentence 的 Component，核對 ground truth 的 Must-retain fact（要實驗設定有 ground_truth）。

用法：
  uv run --locked python -I -m translation_eval judge --config <實驗設定> [--data-dir data]
      [--task mqm|fact] [--dry-run] [--article <id>]... [--limit <N>] [--redo-errors]
不寫 --task 就是兩個任務都跑（沒有 ground_truth 時只有 mqm）。

Judge 只輸出 Finding、Source defect 和 verdict，不輸出分數。輸出裡的每個片段與引用都要通過逐字驗證（連續空白忽略後，必須是
原文或譯文的子字串），不通過的那一條丟掉（verdict 是作廢），記為無效並保留原文；Dimension、Severity、verdict 或類型不在清單內、
結構不符的也一樣。請求的結果記在 judge-calls.jsonl，一個請求一行；已有紀錄的請求會略過，所以可以續跑；fact 的請求在
ground truth 的事實或 anchor 改了之後，舊紀錄過期，會重送。兩次結果的一致性、分數和 Gate 在 report 計算，不在這裡：改了權重
只要重算報告，不用重跑 Judge。
Judge 與被評的 Approach 同家族時拒絕執行，除非實驗設定針對這個組合寫了例外和理由。
"""
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError

from .articles import apply_variant, load_article
from .config import PlannedJudge, load_experiment
from .errors import InputError
from .exchange import send_with_retries
from .ground_truth import Fact, GroundTruth, Sentence, input_hash, load_ground_truth
from .outcome import Interpretation, classify_text
from .pairs import ComponentPair, component_pairs
from .prompts import PromptTemplate, fact_template, judge_template
from .providers import adapter_for
from .schemas import (Dimension, Direction, FactVerdict, InvalidItem, JudgeCall, JudgeFinding, Outcome, RunKey, RunRecord, SourceDefect,
                      Verdict, WeakeningType)
from .store import OutputDir, judge_call_id, judge_call_key
from .terms import LoadedTermList, entries_in
from .textrules import squash
from .translate import select_articles
from .transport import Transport

CALLS_PER_COMPONENT = 2
TASKS = ("mqm", "fact")
SOURCE_ERROR_CORRECTION = "suspected correction of a source error"
MAX_RAW_CHARS = 2000   # 無效的條目保留原文供審閱，但不能無限長
DIRECTION_REQUIREMENTS = {
    Direction.TRAD_TO_EN: "Register: international news English (AP / Reuters style). Criminal charges use the wording of the English "
                          "version of Hong Kong legislation. Amounts keep their original currency; a converted amount given in the "
                          "source is kept in parentheses.",
    Direction.TRAD_TO_SIMPLIFIED: "Register: natural written Mandarin for mainland readers, not a character-by-character conversion. "
                                  "Cantonese quotes keep the strength of the original, including profanity.",
}


@dataclass(frozen=True)
class PlannedCall:
    record: RunRecord
    judge: PlannedJudge
    pairs: list[ComponentPair]
    index: int          # 這個 Component 在 pairs 裡的位置
    call_index: int     # 1 或 2
    task: str = "mqm"
    sentences: tuple[Sentence, ...] = ()   # fact 任務：這個 Component 內的 Sensitive sentence

    @property
    def key(self) -> tuple:
        return judge_call_id(RunKey.of(self.record), self.pairs[self.index].id, self.judge.id, self.task, self.call_index)

    @property
    def input_hash(self) -> str | None:
        return input_hash(list(self.sentences)) if self.task == "fact" else None

    def is_stale(self, recorded: JudgeCall) -> bool:
        """fact 的請求：ground truth 的事實或 anchor 改了，舊紀錄的 verdict 就對不上。"""
        return recorded.input_hash != self.input_hash


def _context(pair: ComponentPair | None) -> dict | None:
    return None if pair is None else {"id": pair.id, "source": pair.source_text, "translation": pair.translated_text}


def user_message(call: PlannedCall, term_list: LoadedTermList) -> str:
    pair, direction = call.pairs[call.index], call.record.direction
    before = call.pairs[call.index - 1] if call.index > 0 else None
    after = call.pairs[call.index + 1] if call.index + 1 < len(call.pairs) else None
    terms = [f"{e.source} -> {' / '.join(e.renderings(direction))}" for e in entries_in(term_list.entries, pair.source_text, direction)]
    return json.dumps({
        "direction": direction.value, "requirements": DIRECTION_REQUIREMENTS[direction], "term_list": terms,
        "context_before": _context(before),
        "component": {"id": pair.id, "type": pair.type, "source": pair.source_text, "translation": pair.translated_text},
        "context_after": _context(after),
    }, ensure_ascii=False)


# ───────────── 輸出的驗證 ─────────────

def _is_verbatim(span: str, text: str) -> bool:
    return squash(span) in squash(text)


def _raw_text(item) -> str:
    """無效的條目保留供審閱：存成 JSON 文字，超過上限就截斷；序列化不了（例如巢狀太深）就說明。"""
    try:
        text = json.dumps(item, ensure_ascii=False)
    except (RecursionError, TypeError, ValueError):
        return "（無法序列化）"
    return text if len(text) <= MAX_RAW_CHARS else text[:MAX_RAW_CHARS] + "（截斷）"


def _build(model, item):
    """模型多給的欄位（例如 confidence）不算結構不符，只看需要的欄位。回傳 (物件, 原因)。"""
    if not isinstance(item, dict):
        return None, "不是物件"
    try:
        return model.model_validate({k: item[k] for k in model.model_fields if k in item}), ""
    except ValidationError as e:
        return None, "結構不符：" + "、".join(str(err["loc"][0]) if err["loc"] else "(根)" for err in e.errors())


def _check_finding(item, pair: ComponentPair) -> tuple[JudgeFinding | None, str]:
    finding, why = _build(JudgeFinding, item)
    if finding is None:
        return None, why
    if not finding.source_span.strip() and not finding.target_span.strip():
        return None, "兩個片段都是空的"
    if finding.source_span.strip() and not _is_verbatim(finding.source_span, pair.source_text):
        return None, "原文片段不是逐字子字串"
    if finding.target_span.strip() and not _is_verbatim(finding.target_span, pair.translated_text or ""):
        return None, "譯文片段不是逐字子字串"
    if finding.subtype.strip().casefold() == SOURCE_ERROR_CORRECTION and finding.dimension is not Dimension.ACCURACY:
        return None, "疑似修正原文錯誤必須是 Accuracy"   # spec：只靠 prompt 不夠，程式要擋
    return finding, ""


def _check_defect(item, pair: ComponentPair) -> tuple[SourceDefect | None, str]:
    defect, why = _build(SourceDefect, item)
    if defect is not None and not _is_verbatim(defect.source_span, pair.source_text):
        return None, "原文片段不是逐字子字串"
    return defect, why


def interpret_output(result: Interpretation, pair: ComponentPair):
    """Judge 的回應 → (結果類別, 說明, 有效的 Finding, 有效的 Source defect, 無效的條目)。
    結構不是 {findings: [...], source_defects: [...]}（後者可以沒有）的，整個輸出是 malformed。"""
    if result.outcome is not Outcome.OK:
        return result.outcome, result.reason, [], [], []
    payload = result.payload
    findings_raw, defects_raw = payload.get("findings"), payload.get("source_defects", [])
    if not isinstance(findings_raw, list) or not isinstance(defects_raw, list):
        return Outcome.MALFORMED, "輸出的結構不符", [], [], []
    findings, defects, invalid = [], [], []
    for item in findings_raw:
        finding, why = _check_finding(item, pair)
        if finding:
            findings.append(finding)
        else:
            invalid.append(InvalidItem(kind="finding", why=why, raw=_raw_text(item)))
    for item in defects_raw:
        defect, why = _check_defect(item, pair)
        if defect:
            defects.append(defect)
        else:
            invalid.append(InvalidItem(kind="source_defect", why=why, raw=_raw_text(item)))
    return Outcome.OK, None, findings, defects, invalid


# ───────────── Must-retain fact ─────────────

def fact_ids(sentences) -> dict[str, Fact]:
    """送給 Judge 的事實 id（F1、F2…，Judge 比較不會抄錯）→ 事實。"""
    return {f"F{n}": fact for n, fact in enumerate((f for s in sentences for f in s.facts), 1)}


def fact_message(call: PlannedCall, term_list: LoadedTermList) -> str:
    pair, direction = call.pairs[call.index], call.record.direction
    terms = [f"{e.source} -> {' / '.join(e.renderings(direction))}" for e in entries_in(term_list.entries, pair.source_text, direction)]
    numbered, sentences = iter(fact_ids(call.sentences)), []
    for sentence in call.sentences:
        sentences.append({"anchor": sentence.anchor, "facts": [{"id": next(numbered), "kind": f.kind.value, "fact": f.text} for f in sentence.facts]})
    return json.dumps({
        "direction": direction.value, "requirements": DIRECTION_REQUIREMENTS[direction], "term_list": terms,
        "component": {"id": pair.id, "type": pair.type, "source": pair.source_text, "translation": pair.translated_text},
        "sentences": sentences,
    }, ensure_ascii=False)


def _read_type(value) -> WeakeningType | None:
    """類型寫成 e、(e)、E 都讀成 e；空的是沒有類型。不認識的丟出 ValueError。"""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError
    return WeakeningType(value.strip().strip("()").strip().lower())


def _check_verdict(item: dict, fact: Fact, pair: ComponentPair) -> tuple[FactVerdict | None, str]:
    try:
        verdict = FactVerdict.model_validate({
            "fact": fact.key, "verdict": item.get("verdict"), "weakening_type": _read_type(item.get("type")),
            "quote": item.get("quote") or "", "reason": item.get("reason") or ""})
    except ValueError:   # pydantic 的 ValidationError 也是 ValueError
        return None, "結構不符"
    weakening = verdict.weakening_type
    amplified = weakening is WeakeningType.AMPLIFICATION
    if (verdict.verdict is Verdict.WEAKENED and amplified) or (verdict.verdict is Verdict.PRESENT and weakening and not amplified) \
            or (verdict.verdict is Verdict.ABSENT and amplified):
        return None, "verdict 與類型矛盾"
    if verdict.verdict is Verdict.ABSENT:
        return verdict.model_copy(update={"quote": ""}), ""
    if not verdict.quote.strip():
        return None, "沒有引用"
    if not _is_verbatim(verdict.quote, pair.translated_text or ""):
        return None, "引用不是譯文的逐字子字串"
    return verdict, ""


def interpret_facts(result: Interpretation, pair: ComponentPair, sentences):
    """Judge 的回應 → (結果類別, 說明, 有效的 verdict, 無效的條目)。結構不是 {verdicts: [...]} 的，整個輸出是 malformed。
    每條要核對的事實，這次請求不是有一個有效的 verdict，就是有一個無效的條目（沒回、重覆或不合格）：作廢的 verdict 要交人手審閱。"""
    if result.outcome is not Outcome.OK:
        return result.outcome, result.reason, [], []
    raw = result.payload.get("verdicts")
    if not isinstance(raw, list):
        return Outcome.MALFORMED, "輸出的結構不符", [], []
    ids, grouped, invalid = fact_ids(sentences), {}, []
    for item in raw:
        fact_id = item.get("fact_id") if isinstance(item, dict) else None
        if fact_id in ids:
            grouped.setdefault(fact_id, []).append(item)
        else:
            invalid.append(InvalidItem(kind="verdict", why="不是物件" if not isinstance(item, dict) else "不認識的 fact_id", raw=_raw_text(item)))
    verdicts = []
    for fact_id, fact in ids.items():
        items = grouped.get(fact_id, [])
        if len(items) == 1:
            verdict, why = _check_verdict(items[0], fact, pair)
            if verdict:
                verdicts.append(verdict)
                continue
        else:
            why = "沒有這條事實的 verdict" if not items else f"同一條事實有 {len(items)} 個 verdict"
        invalid.append(InvalidItem(kind="verdict", why=why, raw=_raw_text(items[0] if len(items) == 1 else items) if items else "", fact=fact.key))
    return Outcome.OK, None, verdicts, invalid


# ───────────── 指令 ─────────────

def plan_calls(experiment, records: list[RunRecord], articles, out: OutputDir, truth: GroundTruth | None, tasks) -> list[PlannedCall]:
    """MQM：每個要翻譯、譯文也有的 Component；fact：每個含 Sensitive sentence、譯文也有的 Component。
    譯文缺少的 Component 沒有東西可以給 Judge：漏段由 check 記為 Finding，Gate 把它的事實直接記為 Absent。"""
    limits = experiment.config.judging.mqm_run_limit
    plan = []
    for record in records:
        if record.outcome is not Outcome.OK:
            continue
        mqm_limit = limits.get(record.approach)
        pairs = component_pairs(apply_variant(articles[record.article_id], record.input_variant), out.read_translation(record))
        for index, pair in enumerate(pairs):
            if pair.translated_text is None:
                continue
            wanted = []
            if "mqm" in tasks and (mqm_limit is None or record.run_index <= mqm_limit):
                wanted.append(("mqm", ()))
            sentences = tuple(truth.sentences_in(record.article_id, pair.id)) if truth else ()
            if "fact" in tasks and sentences:
                wanted.append(("fact", sentences))
            for task, task_sentences in wanted:
                for judge in experiment.judges:
                    plan.extend(PlannedCall(record, judge, pairs, index, n, task, task_sentences) for n in range(1, CALLS_PER_COMPONENT + 1))
    return plan


def require_allowed_families(experiment, plan: list[PlannedCall]) -> None:
    """Judge 與被評的 Approach 同家族時拒絕執行，除非實驗設定針對這個組合寫了例外和理由。baseline 不是 LLM，沒有家族。"""
    excepted = {(e.approach, e.judge) for e in experiment.config.judging.same_family_exceptions}
    clashes, unknown = set(), set()
    for call in plan:
        approach = experiment.approach_config(call.record.approach)
        if approach is None:
            if not call.record.is_baseline:   # baseline 不是 LLM，沒有家族；其他的沒辦法確認家族，不能放行
                unknown.add(call.record.approach)
        elif _family(approach.family) == _family(call.judge.config.family) and (approach.id, call.judge.id) not in excepted:
            clashes.add((approach.id, call.judge.id, approach.family))
    problems = [f"Judge {j} 與 Approach {a} 同家族（{f}）：要在 judging.same_family_exceptions 針對這個組合寫明例外和理由"
                for a, j, f in sorted(clashes)]
    problems += [f"Run 的 Approach {a} 不在實驗設定內，沒辦法檢查它與 Judge 是不是同家族" for a in sorted(unknown)]
    if problems:
        raise InputError(problems)


def _family(name: str) -> str:
    return name.strip().casefold()


def handle(args, transport: Transport) -> int:
    experiment = load_experiment(args.config)
    cfg = experiment.config
    if cfg.judging is None:
        raise InputError("實驗設定沒有 judging，沒有 Judge 可以執行")
    selected = select_articles(cfg.articles.ids, args.article)
    articles = {i: load_article(cfg.articles.dir, i) for i in selected}
    if args.task == "fact" and cfg.ground_truth is None:
        raise InputError("--task fact 要實驗設定有 ground_truth")
    tasks = (args.task,) if args.task else (TASKS if cfg.ground_truth else ("mqm",))
    truth = load_ground_truth(cfg.ground_truth, articles) if cfg.ground_truth else None
    out = OutputDir(args.data_dir, cfg.id)
    records = [r for r in out.require_runs() if r.article_id in articles]
    existing = {judge_call_key(c): c for c in out.read_judge_calls(None)}

    plan = plan_calls(experiment, records, articles, out, truth, tasks)
    require_allowed_families(experiment, plan)
    pending, skipped = [], 0
    for call in plan:
        done = existing.get(call.key)
        if done is None or (args.redo_errors and done.outcome is Outcome.ERROR) or call.is_stale(done):
            pending.append(call)
        else:
            skipped += 1
    if args.limit is not None:
        pending = pending[: args.limit]

    if args.dry_run:
        return dry_run(pending, skipped)

    judges_used = {c.judge.id: c.judge for c in pending}.values()
    missing = sorted({j.config.api_key_env for j in judges_used if not os.environ.get(j.config.api_key_env)})
    if missing:
        raise InputError([f"缺少環境變數 {name}" for name in missing])
    not_ready = sorted({p for j in judges_used for p in adapter_for(j.config).problems(j.config)})
    if not_ready:
        raise InputError(not_ready)

    templates, outcomes = {"mqm": judge_template(), "fact": fact_template()}, Counter()
    for call in pending:
        record, violation = run_call(transport, call, templates[call.task], experiment.term_list, out, replaces_existing=call.key in existing)
        outcomes[record.outcome] += 1
        if violation:   # 資料已經送到不在允許清單內的 provider：不能再送下一個請求
            raise InputError(f"Judge {call.judge.id} 的回應來自不在允許清單內的 provider：已停止，沒有再送出請求（已記錄 {sum(outcomes.values())} 個）")
    summary = "、".join(f"{o} {n}" for o, n in sorted(outcomes.items())) or "沒有要執行的請求"
    print(f"完成 {len(pending)} 個請求（已有紀錄而略過：{skipped}）：{summary}")
    return 0


def dry_run(pending: list[PlannedCall], skipped: int) -> int:
    per_judge, chars = Counter(), Counter()
    templates = {"mqm": judge_template(), "fact": fact_template()}
    # 估計輸入字數需要 Term List，但 dry-run 只是估計：只算 prompt、Component 本身和事實
    for call in pending:
        per_judge[call.judge.id] += 1
        pair = call.pairs[call.index]
        facts = sum(len(f.text) for s in call.sentences for f in s.facts)
        chars[call.judge.id] += len(templates[call.task].text) + len(pair.source_text) + len(pair.translated_text or "") + facts
    print("dry-run：不送出任何請求，也不寫出任何檔案")
    print(f"請求數：{len(pending)}（已有紀錄而略過：{skipped}）")
    for judge_id, n in per_judge.items():
        judge = next(c.judge for c in pending if c.judge.id == judge_id)
        print(f"Judge {judge_id}：請求數 {n}、估計輸入字數約 {chars[judge_id]}")
        print(f"endpoint：{judge_id} {adapter_for(judge.config).endpoint(judge.config)}")
        for line in adapter_for(judge.config).describe(judge.config):
            print(line)
    return 0


def run_call(transport: Transport, call: PlannedCall, template: PromptTemplate, term_list: LoadedTermList, out: OutputDir, *,
             replaces_existing: bool) -> tuple[JudgeCall, bool]:
    """送出一個請求並記錄。第二個回傳值是：回應是不是來自不在允許清單內的 provider。"""
    judge = call.judge.config
    adapter = adapter_for(judge)
    api_key = os.environ[judge.api_key_env]
    pair = call.pairs[call.index]
    message = fact_message(call, term_list) if call.task == "fact" else user_message(call, term_list)
    request = adapter.build_request(judge, template.text, message, api_key)
    exchange = send_with_retries(transport, adapter, request, judge, classify_text)
    result = exchange.interpretation
    findings, defects, verdicts = [], [], []
    if call.task == "fact":
        outcome, reason, verdicts, invalid = interpret_facts(result, pair, call.sentences)
    else:
        outcome, reason, findings, defects, invalid = interpret_output(result, pair)
    record = JudgeCall(
        **RunKey.of(call.record).model_dump(), component_id=pair.id, judge=judge.id, task=call.task, call_index=call.call_index,
        outcome=outcome, reason=reason, attempts=exchange.attempts, verdicts=verdicts, input_hash=call.input_hash,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens, thought_tokens=result.thought_tokens,
        model_version=result.model_version, served_provider=result.served_provider,
        prompt_hash=template.hash, config_hash=call.judge.config_hash,
        findings=findings, source_defects=defects, invalid=invalid,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    written = out.write_judge_call(record, exchange.raw_body, secrets=(api_key,), replaces_existing=replaces_existing)
    return written, result.provider_violation
