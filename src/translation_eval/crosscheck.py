"""crosscheck：Ground truth 交叉檢驗。模型只看文章原文，盲選最敏感的句子、盲抽 Must-retain fact，再與 ground truth 對照，產生分歧清單。

用法：
  uv run --locked python -I -m translation_eval crosscheck --config <實驗設定> [--data-dir data]
      [--dry-run] [--article <id>]... [--limit <N>] [--redo-errors]

實驗設定的 crosscheck.judge 是盲抽的模型（Qwen，經 OpenRouter）；crosscheck.reference_judge 是只在 china-political 的文章另外跑的對照（Gemini）。
兩個都是 judging.judges 裡的 Judge 設定，所以 OpenRouter 的允許清單和資料保護欄位、key 的讀法、重試都與其他請求相同；要有 ground truth。
每篇文章每個模型一個請求（不跑兩次：交叉檢驗是找分歧給 Pan 看，不是判定）。模型收到的只有標題和各 Component 的原文，沒有 ground truth 的任何東西。
模型選的句子必須逐字取自它指的那個 Component 的原文，否則那一條丟掉並計數；抽的事實 kind 要在清單內。結果記在 crosscheck-calls.jsonl，原始回應在
crosscheck-raw/；已有新紀錄的請求略過，所以可以續跑。分歧清單 crosscheck.md 含原文與事實文字，只寫在 data/；終端只印數量。
**crosscheck 從不寫 ground truth 檔**：要不要改由 Pan 決定。對照 Gemini 的結果只作參考，不用來改 ground truth。
"""
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from .articles import load_article, text_of
from .config import Experiment, PlannedJudge, load_experiment
from .errors import InputError
from .ground_truth import GroundTruth, Sentence, load_ground_truth
from .judging import _is_verbatim, _raw_text, provider_violation_stop, send_judge_request
from .outcome import Interpretation
from .prompts import PromptTemplate, crosscheck_template
from .providers import adapter_for
from .schemas import Article, CrosscheckCall, CrosscheckFact, CrosscheckSentence, Domain, FactKind, InvalidItem, Outcome
from .store import OutputDir, crosscheck_call_key
from .textrules import digit_strings, quoted_phrases, squash
from .translate import select_articles
from .transport import Transport

MAX_LISTED_FACTS = 40


@dataclass(frozen=True)
class PlannedCrosscheck:
    article_id: str
    article: Article
    judge: PlannedJudge
    input_hash: str

    @property
    def key(self) -> tuple:
        return (self.article_id, self.judge.id)

    @property
    def message(self) -> str:
        return blind_message(self.article)


def component_texts(article: Article) -> dict[str, str]:
    """模型看得到的 Component：要翻譯、而且有文字的。id 是文章原本的 Component id。"""
    texts = {c.id: text_of(c.type, c.model_dump()) for c in article.components}
    return {cid: text for cid, text in texts.items() if text}


def blind_message(article: Article) -> str:
    """模型收到的全部：標題和各 Component 的原文。沒有 ground truth、沒有 Direction、沒有別的 Judge 的結果。"""
    types = {c.id: c.type for c in article.components}
    return json.dumps({"title": article.title,
                       "components": [{"id": cid, "type": types[cid], "text": text} for cid, text in component_texts(article).items()]}, ensure_ascii=False)


def _build_sentence(item, texts: dict[str, str]) -> tuple[CrosscheckSentence | None, list[InvalidItem]]:
    """一條模型選的句子：先驗證，不合格的整條丟掉；事實的 kind 不在清單內的，那一條事實丟掉。"""
    if not isinstance(item, dict):
        return None, [InvalidItem(kind="crosscheck", why="不是物件", raw=_raw_text(item))]
    component_id, sentence = item.get("component_id"), item.get("sentence")
    if not isinstance(component_id, str) or component_id not in texts:
        return None, [InvalidItem(kind="crosscheck", why="component_id 不在這篇文章", raw=_raw_text(item))]
    if not isinstance(sentence, str) or not sentence.strip() or not _is_verbatim(sentence, texts[component_id]):
        return None, [InvalidItem(kind="crosscheck", why="句子不是那個 Component 原文的逐字子字串", raw=_raw_text(item))]
    facts, invalid = [], []
    for fact in item.get("facts") if isinstance(item.get("facts"), list) else []:
        kind = fact.get("kind") if isinstance(fact, dict) else None
        text = fact.get("fact") if isinstance(fact, dict) else None
        if kind in {k.value for k in FactKind} and isinstance(text, str) and text.strip():
            facts.append(CrosscheckFact(kind=FactKind(kind), fact=text))
        else:
            invalid.append(InvalidItem(kind="crosscheck", why="事實的 kind 不在清單內或沒有文字", raw=_raw_text(fact)))
    if not facts:
        return None, [*invalid, InvalidItem(kind="crosscheck", why="這一句沒有任何有效的事實", raw=_raw_text(item))]
    why = item.get("why")
    return CrosscheckSentence(component_id=component_id, sentence=sentence, why=why if isinstance(why, str) else "", facts=facts), invalid


def interpret_crosscheck(result: Interpretation, texts: dict[str, str]):
    """模型的回應 → (結果類別, 說明, 有效的句子, 無效的條目)。結構不是 {sentences: [...]} 的，整個輸出是 malformed。"""
    if result.outcome is not Outcome.OK:
        return result.outcome, result.reason, [], []
    raw = result.payload.get("sentences")
    if not isinstance(raw, list):
        return Outcome.MALFORMED, "輸出的結構不符", [], []
    sentences, invalid = [], []
    for item in raw:
        sentence, problems = _build_sentence(item, texts)
        invalid += problems
        if sentence is not None:
            sentences.append(sentence)
    return Outcome.OK, None, sentences, invalid


def plan_crosscheck(experiment: Experiment, truth: GroundTruth, articles: dict[str, Article]) -> list[PlannedCrosscheck]:
    """有 ground truth 的文章（含沒有敏感句的對照文章）各用盲抽的模型一次；china-political 的文章另外用對照的模型一次。"""
    cfg = experiment.config.crosscheck
    judges = {j.id: j for j in experiment.judges}
    plan = []
    for article_id, article in articles.items():
        if article_id not in truth.domains:
            continue
        digest = hashlib.sha256(blind_message(article).encode("utf-8")).hexdigest()
        plan.append(PlannedCrosscheck(article_id, article, judges[cfg.judge], digest))
        if cfg.reference_judge and truth.domains[article_id] is Domain.CHINA_POLITICAL:
            plan.append(PlannedCrosscheck(article_id, article, judges[cfg.reference_judge], digest))
    return plan


def is_current(recorded: CrosscheckCall, call: PlannedCrosscheck, template: PromptTemplate) -> bool:
    return (recorded.input_hash, recorded.prompt_hash, recorded.config_hash) == (call.input_hash, template.hash, call.judge.config_hash)


def run_crosscheck(transport: Transport, call: PlannedCrosscheck, template: PromptTemplate, out: OutputDir, *, replaces_existing: bool) -> tuple[CrosscheckCall, bool]:
    """送出一個請求並記錄。第二個回傳值是：回應是不是來自不在允許清單內的 provider。"""
    exchange, api_key = send_judge_request(transport, call.judge, template, call.message)
    result, judge = exchange.interpretation, call.judge.config
    outcome, reason, sentences, invalid = interpret_crosscheck(result, component_texts(call.article))
    record = CrosscheckCall(
        article_id=call.article_id, judge=judge.id, outcome=outcome, reason=reason, attempts=exchange.attempts,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens, thought_tokens=result.thought_tokens,
        model_version=result.model_version, served_provider=result.served_provider, prompt_hash=template.hash, config_hash=call.judge.config_hash,
        input_hash=call.input_hash, sentences=sentences, invalid=invalid, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return out.write_crosscheck_call(record, exchange.raw_body, secrets=(api_key,), replaces_existing=replaces_existing), result.provider_violation


# ───────────── 與 ground truth 對照 ─────────────

@dataclass(frozen=True)
class Pairing:
    truth: Sentence
    model: tuple[CrosscheckSentence, ...]


@dataclass(frozen=True)
class Comparison:
    both: list[Pairing]
    model_only: list[CrosscheckSentence]
    truth_only: list[Sentence]


def _interval(text: str, span: str) -> tuple[int, int] | None:
    squashed, wanted = squash(text), squash(span)
    start = squashed.find(wanted) if wanted else -1
    return None if start < 0 else (start, start + len(wanted))


def overlaps(texts: dict[str, str], component_id: str, first: str, second: str) -> bool:
    """同一個 Component 裡，兩段文字（忽略空白）的位置有重疊。ground truth 的 anchor 可能只是句子的一段，模型選的是整句。"""
    a, b = _interval(texts[component_id], first), _interval(texts[component_id], second)
    return a is not None and b is not None and a[0] < b[1] and b[0] < a[1]


def compare(texts: dict[str, str], truth: list[Sentence], model: list[CrosscheckSentence]) -> Comparison:
    """一篇文章：兩邊都有（同一個 Component、位置重疊）、只有模型選了、只有 ground truth 有。"""
    both, matched = [], set()
    for sentence in truth:
        hits = [m for m in model if m.component_id == sentence.component_id and overlaps(texts, m.component_id, m.sentence, sentence.anchor)]
        matched.update(id(m) for m in hits)
        if hits:
            both.append(Pairing(sentence, tuple(hits)))
    truth_only = [s for s in truth if not any(p.truth is s for p in both)]
    return Comparison(both, [m for m in model if id(m) not in matched], truth_only)


def number_and_quote_differences(truth_facts: list[str], model_facts: list[str]) -> list[str]:
    """兩邊事實的阿拉伯數字和引號內文字的差異。中文數字（三名、十年）不比。"""
    out = []
    for label, extract in (("數字", digit_strings), ("引語", quoted_phrases)):
        truth_side = set().union(*(extract(f) for f in truth_facts)) if truth_facts else set()
        model_side = set().union(*(extract(f) for f in model_facts)) if model_facts else set()
        if truth_side != model_side:
            only_truth, only_model = sorted(truth_side - model_side), sorted(model_side - truth_side)
            out.append(f"{label}差異：只有 ground truth 有 {'、'.join(only_truth) or '無'}；只有模型有 {'、'.join(only_model) or '無'}")
    return out


def reference_only_facts(texts: dict[str, str], reference: list[CrosscheckSentence], blind: list[CrosscheckSentence]) -> list[tuple[CrosscheckSentence, list[CrosscheckFact], str]]:
    """對照模型（Gemini）抽到、盲抽的模型（Qwen）沒抽到的事實：整句沒選到的，列出那一句的全部事實；兩邊都選了的句子，列出盲抽的模型沒有
    「同一種 kind、而且數字與引語都涵蓋」的事實。這是粗略的啟發式，只作參考。"""
    out = []
    for sentence in reference:
        overlapping = [m for m in blind if m.component_id == sentence.component_id and overlaps(texts, m.component_id, m.sentence, sentence.sentence)]
        if not overlapping:
            out.append((sentence, sentence.facts, "盲抽的模型沒有選這一句"))
            continue
        blind_facts = [f for m in overlapping for f in m.facts]
        missing = [f for f in sentence.facts if not any(
            b.kind is f.kind and digit_strings(f.fact) <= digit_strings(b.fact) and quoted_phrases(f.fact) <= quoted_phrases(b.fact) for b in blind_facts)]
        if missing:
            out.append((sentence, missing, "兩邊都選了這一句，但盲抽的模型沒有抽到這些事實"))
    return out


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _facts(facts) -> list[str]:
    return [f"{f.kind.value}：{_one_line(f.text if hasattr(f, 'text') else f.fact)}" for f in facts]


def current_calls(experiment: Experiment, truth: GroundTruth, articles: dict[str, Article], calls: list[CrosscheckCall]) -> dict[tuple, CrosscheckCall]:
    """還適用的請求紀錄（文章、prompt、Judge 設定都沒變），按（文章 id, Judge）。過期的不用：舊的盲抽不能拿來對現在的文章。"""
    template = crosscheck_template()
    planned = {c.key: c for c in plan_crosscheck(experiment, truth, articles)}
    return {crosscheck_call_key(c): c for c in calls if crosscheck_call_key(c) in planned and is_current(c, planned[crosscheck_call_key(c)], template)}


def render(experiment: Experiment, truth: GroundTruth, articles: dict[str, Article], calls: list[CrosscheckCall]) -> str:
    cfg = experiment.config.crosscheck
    fresh = current_calls(experiment, truth, articles, calls)
    judges = {j.id: j for j in experiment.judges}
    lines = [f"# Ground truth 交叉檢驗：{experiment.config.id}", "",
             f"- Ground truth：{truth.ref.status.value}；hash {truth.hash}（只讀，crosscheck 不改動它；要不要改由 Pan 決定）",
             f"- 盲抽的模型：Judge {cfg.judge}（{judges[cfg.judge].config.model}）" + (f"；對照：Judge {cfg.reference_judge}（{judges[cfg.reference_judge].config.model}），"
                                                                           "只在 china-political 的文章跑，結果只作參考，不用來改 ground truth" if cfg.reference_judge else ""),
             "- 模型只看到文章原文；選的句子逐字取自原文，不合格的丟掉並計數。含原文與事實文字，只在本機看。", ""]
    totals = Counter()
    sections = []
    for article_id in articles:
        if article_id not in truth.domains:
            continue
        article, texts = articles[article_id], component_texts(articles[article_id])
        call = fresh.get((article_id, cfg.judge))
        body = [f"## 文章 {article_id}（{truth.domains[article_id].value}）", ""]
        if call is None or call.outcome is not Outcome.OK:
            reason = "還沒有結果" if call is None else f"請求結果是 {call.outcome.value}" + (f"（{_one_line(call.reason)}）" if call.reason else "")
            sections.append(body + [f"- 盲抽的模型：{reason}", ""])
            totals["沒有結果的文章"] += 1
            continue
        comparison = compare(texts, truth.sentences_for(article_id), call.sentences)
        totals.update({"兩邊都有的句子": len(comparison.both), "只有模型選了的句子": len(comparison.model_only), "只有 ground truth 有的句子": len(comparison.truth_only),
                       "無效的條目": len(call.invalid)})
        body += [f"- 兩邊都有 {len(comparison.both)}、只有模型選了 {len(comparison.model_only)}、只有 ground truth 有 {len(comparison.truth_only)}；模型的無效條目 {len(call.invalid)}", ""]
        if comparison.both:
            body += ["### 兩邊都有的句子", ""]
            for pairing in comparison.both:
                model_facts = [f for m in pairing.model for f in m.facts]
                body += [f"- `{pairing.truth.component_id}` ・ ground truth 的句子：「{_one_line(pairing.truth.anchor)}」", *[f"  - 模型選的句子：「{_one_line(m.sentence)}」" for m in pairing.model],
                         *[f"  - ground truth 事實 {line}" for line in _facts(pairing.truth.facts)], *[f"  - 模型事實 {line}" for line in _facts(model_facts)],
                         *[f"  - {d}" for d in number_and_quote_differences([f.text for f in pairing.truth.facts], [f.fact for f in model_facts])], ""]
        if comparison.model_only:
            body += ["### 只有模型選了的句子", ""]
            for m in comparison.model_only:
                body += [f"- `{m.component_id}` ・「{_one_line(m.sentence)}」（{_one_line(m.why) or '沒有寫原因'}）", *[f"  - 模型事實 {line}" for line in _facts(m.facts)], ""]
        if comparison.truth_only:
            body += ["### 只有 ground truth 有的句子", ""]
            for s in comparison.truth_only:
                body += [f"- `{s.component_id}` ・「{_one_line(s.anchor)}」", *[f"  - ground truth 事實 {line}" for line in _facts(s.facts)], ""]
        reference = fresh.get((article_id, cfg.reference_judge)) if cfg.reference_judge else None
        if reference is not None and reference.outcome is Outcome.OK:
            extra = reference_only_facts(texts, reference.sentences, call.sentences)
            body += [f"### 參考：Judge {cfg.reference_judge} 抽到、Judge {cfg.judge} 沒抽到的事實（只作參考，不用來改 ground truth）", ""]
            for sentence, facts, why in extra[:MAX_LISTED_FACTS]:
                body += [f"- `{sentence.component_id}` ・「{_one_line(sentence.sentence)}」：{why}", *[f"  - {line}" for line in _facts(facts)]]
            body += ([f"- …另有 {len(extra) - MAX_LISTED_FACTS} 句沒有列出"] if len(extra) > MAX_LISTED_FACTS else []) + ([] if extra else ["沒有。"]) + [""]
            totals["參考：Gemini 抽到而 Qwen 沒抽到的句子"] += len(extra)
        elif reference is not None:
            body += [f"- 對照的模型 Judge {cfg.reference_judge}：請求結果是 {reference.outcome.value}", ""]
        sections.append(body)
    lines += ["## 摘要（只有數字）", "", *[f"- {name}：{n}" for name, n in totals.items()], ""]
    for body in sections:
        lines += body
    return "\n".join(lines).rstrip("\n") + "\n"


# ───────────── 指令 ─────────────

def handle(args, transport: Transport) -> int:
    experiment = load_experiment(args.config)
    cfg = experiment.config
    if cfg.crosscheck is None:
        raise InputError("實驗設定沒有 crosscheck")
    selected = select_articles(cfg.articles.ids, args.article)
    articles = {i: load_article(cfg.articles.dir, i) for i in cfg.articles.ids}
    truth = load_ground_truth(cfg.ground_truth, articles)
    out = OutputDir(args.data_dir, cfg.id)
    template = crosscheck_template()
    existing = {crosscheck_call_key(c): c for c in out.read_crosscheck_calls()}

    pending, skipped = [], 0
    for call in plan_crosscheck(experiment, truth, {i: articles[i] for i in selected}):
        done = existing.get(call.key)
        if done is None or (args.redo_errors and done.outcome is Outcome.ERROR) or not is_current(done, call, template):
            pending.append(call)
        else:
            skipped += 1
    if args.limit is not None:
        pending = pending[: args.limit]

    if args.dry_run:
        return dry_run(pending, skipped, template)
    judges_used = {c.judge.id: c.judge for c in pending}.values()
    missing = sorted({j.config.api_key_env for j in judges_used if not os.environ.get(j.config.api_key_env)})
    if missing:
        raise InputError([f"缺少環境變數 {name}" for name in missing])
    not_ready = sorted({p for j in judges_used for p in adapter_for(j.config).problems(j.config)})
    if not_ready:
        raise InputError(not_ready)
    outcomes = Counter()
    for call in pending:
        record, violation = run_crosscheck(transport, call, template, out, replaces_existing=call.key in existing)
        outcomes[record.outcome] += 1
        if violation:   # 資料已經送到不在允許清單內的 provider：不能再送下一個請求
            raise provider_violation_stop(call.judge.id, sum(outcomes.values()))
    print(f"完成 {len(pending)} 個請求（已有新的紀錄而略過：{skipped}）：" + ("、".join(f"{o} {n}" for o, n in sorted(outcomes.items())) or "沒有要執行的請求"))
    out.write_crosscheck(render(experiment, truth, articles, out.read_crosscheck_calls()))
    print(f"已寫入 {out.crosscheck_path}")
    return 0


def dry_run(pending: list[PlannedCrosscheck], skipped: int, template: PromptTemplate) -> int:
    print("dry-run：不送出任何請求，也不寫出任何檔案")
    print(f"請求數：{len(pending)}（已有新的紀錄而略過：{skipped}）")
    per_judge = Counter(c.judge.id for c in pending)
    for judge_id, n in per_judge.items():
        judge = next(c.judge for c in pending if c.judge.id == judge_id)
        chars = sum(len(template.text) + len(c.message) for c in pending if c.judge.id == judge_id)
        print(f"Judge {judge_id}：請求數 {n}、估計輸入字數約 {chars}")
        print(f"endpoint：{judge_id} {adapter_for(judge.config).endpoint(judge.config)}")
        for line in adapter_for(judge.config).describe(judge.config):
            print(line)
    return 0
