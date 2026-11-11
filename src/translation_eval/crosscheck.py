"""crosscheck：Ground truth 交叉檢驗。模型只看文章原文，盲選最敏感的句子、盲抽 Must-retain fact，再與 ground truth 對照，產生分歧清單。

用法：
  uv run --locked python -I -m translation_eval crosscheck --config <實驗設定> [--data-dir data]
      [--dry-run] [--article <id>]... [--limit <N>] [--redo-errors]

實驗設定的 crosscheck.judge 是盲抽的模型（Qwen，經 OpenRouter）；crosscheck.reference_judge 是只在 china-political 的文章另外跑的對照（Gemini）。
兩個都是 judging.judges 裡的 Judge 設定，所以 OpenRouter 的允許清單和資料保護欄位、key 的讀法、重試都與其他請求相同；要有 ground truth。
每篇文章每個模型一個請求（不跑兩次：交叉檢驗是找分歧給 Pan 看，不是判定）。模型收到的只有標題和各 Component 的原文，沒有 ground truth 的任何東西。
模型選的句子必須逐字取自它指的那個 Component 的原文（同一個欄位內，忽略空白），否則那一條丟掉並計數；抽的事實 kind 要在清單內；同一句選兩次只留一個。結果記在 crosscheck-calls.jsonl，原始回應在
crosscheck-raw/；已有新紀錄的請求略過，所以可以續跑。分歧清單 crosscheck.md 含原文與事實文字，只寫在 data/；終端只印數量。
模型被擋或失敗（block、error 等）本身就是結果，與「評了但漏看」不同：分歧清單明講，對照模型的結果照樣列出。
**crosscheck 從不寫 ground truth 檔**：要不要改由 Pan 決定。china-political 這一組（Qwen 是被測方，單看它分不出「ground truth 寫多了」
和「Qwen 自己漏看」）的結果，不論對照 Gemini 與否，都只作參考，不用來改 ground truth。
"""
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError

from .articles import load_article, text_of
from .config import Experiment, PlannedJudge, load_experiment
from .errors import InputError, provider_violation_stop
from .ground_truth import GroundTruth, Sentence, load_ground_truth
from .judging import (is_verbatim, print_endpoint, raw_text, redone_from, require_judges_ready, select_pending, send_judge_request)
from .outcome import Interpretation
from .prompts import PromptTemplate, crosscheck_template
from .schemas import (Article, CrosscheckCall, CrosscheckSentence, Domain, GtFact, InvalidItem, InvalidKind, Outcome, REDO_OUTCOMES)
from .store import OutputDir, crosscheck_call_key
from .textrules import one_line, chinese_numbers, digit_strings, quoted_phrases, squash
from .translate import select_articles
from .transport import Transport

MAX_SENTENCES = 10   # prompt 要求的上限（crosscheck.txt 寫 at most 10）：模型選滿了，「只有 ground truth 有」可能是上限造成的
MAX_LISTED_GAPS = 40
COVERAGE = 0.5       # 對照模型的一條事實，盲抽的模型有一條事實涵蓋它的字元二連字的這個比例，就算涵蓋（粗略的啟發式，只作參考）
FACT_DROPPED = "事實的 kind 不在清單內或沒有文字"


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
    """模型收到的全部：標題和各 Component 的原文。沒有 ground truth、沒有 Direction、沒有別的模型的結果。"""
    types = {c.id: c.type for c in article.components}
    return json.dumps({"title": article.title,
                       "components": [{"id": cid, "type": types[cid], "text": text} for cid, text in component_texts(article).items()]}, ensure_ascii=False)


def _verbatim_in_one_field(sentence: str, text: str) -> bool:
    """句子要整句落在 Component 的一個欄位內：quote 的出處、gallery 的多個說明等用換行接在一起，忽略空白的比對不可以跨過欄位的邊界。"""
    return any(is_verbatim(sentence, field) for field in text.split("\n") if field.strip())


def _dropped(why: str, item) -> InvalidItem:
    return InvalidItem(kind=InvalidKind.CROSSCHECK, why=why, raw=raw_text(item))


def _build_sentence(item, texts: dict[str, str]) -> tuple[CrosscheckSentence | None, list[InvalidItem]]:
    """一條模型選的句子：先驗證，不合格的整條丟掉；事實的 kind 不在清單內的，那一條事實丟掉。"""
    if not isinstance(item, dict):
        return None, [_dropped("不是物件", item)]
    component_id, sentence = item.get("component_id"), item.get("sentence")
    if not isinstance(component_id, str) or component_id not in texts:
        return None, [_dropped("component_id 不在這篇文章", item)]
    if not isinstance(sentence, str) or not sentence.strip() or not _verbatim_in_one_field(sentence, texts[component_id]):
        return None, [_dropped("句子不是那個 Component 原文的逐字子字串", item)]
    facts, invalid = [], []
    for fact in item.get("facts") if isinstance(item.get("facts"), list) else []:
        try:
            facts.append(GtFact.model_validate({"kind": fact.get("kind"), "fact": fact.get("fact")}))
        except (ValidationError, AttributeError):
            invalid.append(_dropped(FACT_DROPPED, fact))
    if not facts:
        return None, [*invalid, _dropped("這一句沒有任何有效的事實", item)]
    why = item.get("why")
    return CrosscheckSentence(component_id=component_id, sentence=sentence, why=why if isinstance(why, str) else "", facts=facts), invalid


def _merge_duplicates(sentences: list[CrosscheckSentence]) -> list[CrosscheckSentence]:
    """同一個 Component 的同一句選了兩次，只留第一個，事實合併（不重覆）：不然句子與事實的數量都會灌大。"""
    merged: dict[tuple, CrosscheckSentence] = {}
    for sentence in sentences:
        key = (sentence.component_id, squash(sentence.sentence))
        if key not in merged:
            merged[key] = sentence
            continue
        known = {(f.kind, squash(f.fact)) for f in merged[key].facts}
        extra = [f for f in sentence.facts if (f.kind, squash(f.fact)) not in known]
        merged[key] = merged[key].model_copy(update={"facts": [*merged[key].facts, *extra]})
    return list(merged.values())


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
    return Outcome.OK, None, _merge_duplicates(sentences), invalid


def dropped_counts(call: CrosscheckCall) -> tuple[int, int]:
    """(丟掉的句子數, 丟掉的事實數)。一句有兩條壞事實再加上整句被丟，是 1 句、2 條事實。"""
    facts = sum(1 for i in call.invalid if i.why == FACT_DROPPED)
    return len(call.invalid) - facts, facts


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


def run_crosscheck(transport: Transport, call: PlannedCrosscheck, template: PromptTemplate, out: OutputDir, *, replaced: CrosscheckCall | None) -> tuple[CrosscheckCall, bool]:
    """送出一個請求並記錄。replaced：被它取代的舊紀錄（沒有就是 None）。第二個回傳值是：回應是不是來自不在允許清單內的 provider。"""
    exchange, api_key = send_judge_request(transport, call.judge, template, call.message)
    result, judge = exchange.interpretation, call.judge.config
    outcome, reason, sentences, invalid = interpret_crosscheck(result, component_texts(call.article))
    record = CrosscheckCall(
        article_id=call.article_id, judge=judge.id, outcome=outcome, reason=reason, attempts=exchange.attempts,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens, thought_tokens=result.thought_tokens,
        model_version=result.model_version, served_provider=result.served_provider, prompt_hash=template.hash, config_hash=call.judge.config_hash,
        input_hash=call.input_hash, sentences=sentences, invalid=invalid, redone_from=redone_from(replaced),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return out.write_crosscheck_call(record, exchange.raw_body, secrets=(api_key,), replaces_existing=replaced is not None), result.provider_violation


# ───────────── 與 ground truth 對照 ─────────────

@dataclass(frozen=True)
class Group:
    """互相重疊的 ground truth 句子與模型句子。一個模型句子可以蓋住好幾個 ground truth 的 anchor，反之亦然：連在一起的放同一組，事實才不會對錯人。"""
    truth: tuple[Sentence, ...]
    model: tuple[CrosscheckSentence, ...]


@dataclass(frozen=True)
class Comparison:
    both: list[Group]
    model_only: list[CrosscheckSentence]
    truth_only: list[Sentence]


@dataclass(frozen=True)
class ReferenceGap:
    sentence: CrosscheckSentence
    facts: list[GtFact]
    why: str


def _intervals(text: str, span: str) -> list[tuple[int, int]]:
    """span（忽略空白）在 text 裡每一次出現的位置。"""
    squashed, wanted = squash(text), squash(span)
    found, start = [], squashed.find(wanted) if wanted else -1
    while start >= 0:
        found.append((start, start + len(wanted)))
        start = squashed.find(wanted, start + 1)
    return found


def overlaps(text: str, first: str, second: str) -> bool:
    """同一個 Component 的文字裡，兩段文字的位置有重疊（只是相鄰不算）。任何一次出現互相重疊就算。ground truth 的 anchor 可能只是句子的一段，
    模型選的是整句，所以不要求相等。"""
    return any(a[0] < b[1] and b[0] < a[1] for a in _intervals(text, first) for b in _intervals(text, second))


def compare(texts: dict[str, str], truth: list[Sentence], model: list[CrosscheckSentence]) -> Comparison:
    """一篇文章：兩邊都有（同一個 Component、位置重疊，連在一起的成一組）、只有模型選了、只有 ground truth 有。"""
    parent = list(range(len(truth) + len(model)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, t in enumerate(truth):
        for j, m in enumerate(model):
            if t.component_id == m.component_id and overlaps(texts[t.component_id], t.anchor, m.sentence):
                parent[find(i)] = find(len(truth) + j)
    members = defaultdict(list)
    for index in range(len(parent)):
        members[find(index)].append(index)
    both, model_only, truth_only = [], [], []
    for indexes in members.values():
        t_side = tuple(truth[i] for i in indexes if i < len(truth))
        m_side = tuple(model[i - len(truth)] for i in indexes if i >= len(truth))
        (both if t_side and m_side else truth_only if t_side else model_only).append(Group(t_side, m_side) if t_side and m_side else (t_side or m_side))
    return Comparison(both, [m for group in model_only for m in group], [t for group in truth_only for t in group])


def number_and_quote_differences(truth_facts: list[str], model_facts: list[str]) -> list[str]:
    """兩邊事實的阿拉伯數字、接量詞的中文數字（三名、十年）和引號內文字的差異。「30 萬」與「300,000」會被標出來：寫法不同要人看。"""
    out = []
    for label, extract in (("數字", digit_strings), ("中文數字", chinese_numbers), ("引語", quoted_phrases)):
        truth_side = set().union(*(extract(f) for f in truth_facts)) if truth_facts else set()
        model_side = set().union(*(extract(f) for f in model_facts)) if model_facts else set()
        if truth_side != model_side:
            only_truth, only_model = sorted(truth_side - model_side), sorted(model_side - truth_side)
            out.append(f"{label}差異：只有 ground truth 有 {'、'.join(only_truth) or '無'}；只有模型有 {'、'.join(only_model) or '無'}")
    return out


def _bigrams(text: str) -> set[str]:
    squashed = squash(text)
    return {squashed[i:i + 2] for i in range(len(squashed) - 1)} or {squashed}


def covers(blind: GtFact, fact: GtFact) -> bool:
    """盲抽的一條事實涵蓋對照模型的一條事實：數字、中文數字、引語都涵蓋，而且字元二連字重疊到 COVERAGE。不看 kind：同一件事兩個模型可能標成不同的種類。"""
    ours, theirs = fact.fact, blind.fact
    if not (digit_strings(ours) <= digit_strings(theirs) and chinese_numbers(ours) <= chinese_numbers(theirs) and quoted_phrases(ours) <= quoted_phrases(theirs)):
        return False
    wanted = _bigrams(ours)
    return len(wanted & _bigrams(theirs)) / len(wanted) >= COVERAGE


def reference_gaps(texts: dict[str, str], reference: list[CrosscheckSentence], blind: list[CrosscheckSentence]) -> list[ReferenceGap]:
    """對照模型（Gemini）抽到、盲抽的模型（Qwen）沒抽到的事實：整句沒選到的，列出那一句的全部事實；兩邊都選了的句子，列出沒有被涵蓋（covers）
    的事實。只作參考。"""
    gaps = []
    for sentence in reference:
        overlapping = [m for m in blind if m.component_id == sentence.component_id and overlaps(texts[m.component_id], m.sentence, sentence.sentence)]
        if not overlapping:
            gaps.append(ReferenceGap(sentence, sentence.facts, "盲抽的模型沒有選這一句"))
            continue
        missing = [f for f in sentence.facts if not any(covers(b, f) for m in overlapping for b in m.facts)]
        if missing:
            gaps.append(ReferenceGap(sentence, missing, "兩邊都選了這一句，但盲抽的模型沒有抽到這些事實"))
    return gaps


# ───────────── 分歧清單 ─────────────

def current_calls(experiment: Experiment, truth: GroundTruth, articles: dict[str, Article], calls: list[CrosscheckCall]) -> dict[tuple, CrosscheckCall]:
    """還適用的請求紀錄（文章、prompt、Judge 設定都沒變），按（文章 id, Judge）。過期的不用：舊的盲抽不能拿來對現在的文章。"""
    template = crosscheck_template()
    planned = {c.key: c for c in plan_crosscheck(experiment, truth, articles)}
    return {crosscheck_call_key(c): c for c in calls if crosscheck_call_key(c) in planned and is_current(c, planned[crosscheck_call_key(c)], template)}


def _truth_fact_lines(facts) -> list[str]:
    return [f"{f.kind.value}：{one_line(f.text)}" for f in facts]


def _model_fact_lines(facts) -> list[str]:
    return [f"{f.kind.value}：{one_line(f.fact)}" for f in facts]


def call_status(call: CrosscheckCall | None) -> str:
    """一個請求的結果，一行。被擋、拒絕這類本身就是結果，不是「沒有結果」；error 與 empty 是沒有拿到結果，要重做。"""
    if call is None:
        return "還沒有結果（尚未跑，或請求已過期）"
    if call.outcome is not Outcome.OK:
        what = f"請求結果是 {call.outcome.value}" + (f"（{one_line(call.reason)}）" if call.reason else "")
        if call.outcome in REDO_OUTCOMES:
            return what + "：沒有拿到結果，先用 --redo-errors 重做，與「評了但漏看」不同"
        return what + "：這本身就是結果，與「評了但漏看」不同"
    dropped_sentences, dropped_facts = dropped_counts(call)
    full = f"；選滿 {MAX_SENTENCES} 句（ground truth 的句子更多時，「只有 ground truth 有」可能是上限造成的）" if len(call.sentences) >= MAX_SENTENCES else ""
    return f"選了 {len(call.sentences)} 句；丟掉的句子 {dropped_sentences}、丟掉的事實 {dropped_facts}（句子不是逐字取自原文，或事實的 kind 不合）{full}"


def _group_lines(group: Group) -> list[str]:
    model_facts = [f for m in group.model for f in m.facts]
    truth_facts = [f for t in group.truth for f in t.facts]
    lines = [f"- `{group.truth[0].component_id}`" + "".join(f" ・ ground truth 的句子：「{one_line(t.anchor)}」" for t in group.truth)]
    lines += [f"  - 模型選的句子：「{one_line(m.sentence)}」" for m in group.model]
    lines += [f"  - ground truth 事實 {line}" for line in _truth_fact_lines(truth_facts)] + [f"  - 模型事實 {line}" for line in _model_fact_lines(model_facts)]
    lines += [f"  - {d}" for d in number_and_quote_differences([f.text for f in truth_facts], [f.fact for f in model_facts])]
    return lines + [""]


def _comparison_lines(comparison: Comparison) -> list[str]:
    lines = []
    if comparison.both:
        lines += ["### 兩邊都有的句子", "", *[line for group in comparison.both for line in _group_lines(group)]]
    if comparison.model_only:
        lines += ["### 只有模型選了的句子", ""]
        for m in comparison.model_only:
            lines += [f"- `{m.component_id}` ・「{one_line(m.sentence)}」（{one_line(m.why) or '沒有寫原因'}）", *[f"  - 模型事實 {line}" for line in _model_fact_lines(m.facts)], ""]
    if comparison.truth_only:
        lines += ["### 只有 ground truth 有的句子", ""]
        for s in comparison.truth_only:
            lines += [f"- `{s.component_id}` ・「{one_line(s.anchor)}」", *[f"  - ground truth 事實 {line}" for line in _truth_fact_lines(s.facts)], ""]
    return lines


def _reference_lines(cfg, texts: dict[str, str], reference: CrosscheckCall, blind: CrosscheckCall | None) -> list[str]:
    blind_ok = blind is not None and blind.outcome is Outcome.OK
    gaps = reference_gaps(texts, reference.sentences, blind.sentences if blind_ok else [])
    note = "" if blind_ok else f"（盲抽的模型 Judge {cfg.judge} 沒有結果，所以對照模型的全部事實都列出）"
    lines = [f"### 參考：Judge {cfg.reference_judge} 抽到、Judge {cfg.judge} 沒抽到的事實{note}（只作參考，不用來改 ground truth）", ""]
    for gap in gaps[:MAX_LISTED_GAPS]:
        lines += [f"- `{gap.sentence.component_id}` ・「{one_line(gap.sentence.sentence)}」：{gap.why}", *[f"  - {line}" for line in _model_fact_lines(gap.facts)]]
    if len(gaps) > MAX_LISTED_GAPS:
        lines.append(f"- …另有 {len(gaps) - MAX_LISTED_GAPS} 句沒有列出")
    return lines + ([] if gaps else ["沒有。"]) + [""]


def _article_lines(cfg, truth: GroundTruth, article_id: str, article: Article, fresh: dict, totals: dict) -> list[str]:
    domain, texts = truth.domains[article_id], component_texts(article)
    blind, reference = fresh.get((article_id, cfg.judge)), fresh.get((article_id, cfg.reference_judge)) if cfg.reference_judge else None
    lines = [f"## 文章 {article_id}（{domain.value}）", ""]
    if domain is Domain.CHINA_POLITICAL:
        lines += ["- **這一組只作參考，不用來改 ground truth**：Qwen 是被測方，單看它的結果分不出「ground truth 寫多了」和「Qwen 自己漏看」。", ""]
    lines.append(f"- 盲抽的模型 Judge {cfg.judge}：{call_status(blind)}")
    if cfg.reference_judge and domain is Domain.CHINA_POLITICAL:
        lines.append(f"- 對照的模型 Judge {cfg.reference_judge}：{call_status(reference)}")
    lines.append("")
    counter = totals[domain]
    if blind is None or blind.outcome is not Outcome.OK:
        counter["沒有結果的文章"][blind.outcome.value if blind is not None else "還沒有跑"] += 1
    else:
        comparison = compare(texts, truth.sentences_for(article_id), blind.sentences)
        counter["兩邊都有的句子"]["n"] += len(comparison.both)
        counter["只有模型選了的句子"]["n"] += len(comparison.model_only)
        counter["只有 ground truth 有的句子"]["n"] += len(comparison.truth_only)
        counter["丟掉的句子"]["n"] += dropped_counts(blind)[0]
        counter["丟掉的事實"]["n"] += dropped_counts(blind)[1]
        lines += [f"- 兩邊都有 {len(comparison.both)}、只有模型選了 {len(comparison.model_only)}、只有 ground truth 有 {len(comparison.truth_only)}", "", *_comparison_lines(comparison)]
    if cfg.reference_judge and domain is Domain.CHINA_POLITICAL and reference is not None and reference.outcome is Outcome.OK:
        lines += _reference_lines(cfg, texts, reference, blind)
        counter["參考：對照模型抽到而盲抽的模型沒抽到的句子"]["n"] += len(reference_gaps(texts, reference.sentences, blind.sentences if blind is not None and blind.outcome is Outcome.OK else []))
    return lines


def _totals_lines(totals: dict) -> list[str]:
    lines = ["## 數量（只有數字）", ""]
    for domain, counter in totals.items():
        parts = []
        for name, counted in counter.items():
            parts.append(f"{name} {'、'.join(f'{k} {n}' for k, n in counted.items())}" if name == "沒有結果的文章" else f"{name} {counted['n']}")
        caveat = "（只作參考，不用來改 ground truth）" if domain is Domain.CHINA_POLITICAL else ""
        lines.append(f"- {domain.value}{caveat}：" + ("；".join(parts) or "沒有文章"))
    return lines + [""]


def render(experiment: Experiment, truth: GroundTruth, articles: dict[str, Article], calls: list[CrosscheckCall]) -> str:
    """分歧清單。含原文與事實文字，只寫在 data/。"""
    cfg = experiment.config.crosscheck
    fresh = current_calls(experiment, truth, articles, calls)
    judges = {j.id: j for j in experiment.judges}
    without_truth = [a for a in articles if a not in truth.domains]
    lines = [f"# Ground truth 交叉檢驗：{experiment.config.id}", "",
             f"- Ground truth：{truth.ref.status.value}；hash {truth.hash}（只讀，crosscheck 不改動它；要不要改由 Pan 決定）",
             f"- 盲抽的模型：Judge {cfg.judge}（{judges[cfg.judge].config.model}）" + (f"；對照：Judge {cfg.reference_judge}（{judges[cfg.reference_judge].config.model}），"
                                                                           "只在 china-political 的文章跑" if cfg.reference_judge else ""),
             f"- 實驗內沒有 ground truth 的文章（不送給模型）：{'、'.join(without_truth) or '無'}",
             f"- 模型只看到文章原文；選的句子逐字取自原文，不合格的丟掉並計數；每篇最多選 {MAX_SENTENCES} 句。含原文與事實文字，只在本機看。", ""]
    totals: dict[Domain, dict] = {}
    body: list[str] = []
    for article_id, article in articles.items():
        if article_id in truth.domains:
            totals.setdefault(truth.domains[article_id], defaultdict(Counter))
            body += _article_lines(cfg, truth, article_id, article, fresh, totals)
    return "\n".join(lines + _totals_lines(totals) + body).rstrip("\n") + "\n"


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

    plan = plan_crosscheck(experiment, truth, {i: articles[i] for i in selected})
    pending, skipped = select_pending(plan, existing, redo_errors=args.redo_errors, limit=args.limit, is_stale=lambda recorded, call: not is_current(recorded, call, template))

    if args.dry_run:
        return dry_run(pending, skipped, template)
    require_judges_ready([c.judge for c in pending])
    outcomes = Counter()
    for call in pending:
        record, violation = run_crosscheck(transport, call, template, out, replaced=existing.get(call.key))
        outcomes[record.outcome] += 1
        if violation:   # 資料已經送到不在允許清單內的 provider：不能再送下一個請求
            raise provider_violation_stop(f"Judge {call.judge.id}", sum(outcomes.values()))
    print(f"完成 {len(pending)} 個請求（已有新的紀錄而略過：{skipped}）：" + ("、".join(f"{o} {n}" for o, n in sorted(outcomes.items())) or "沒有要執行的請求"))
    out.write_crosscheck(render(experiment, truth, articles, out.read_crosscheck_calls()))
    print(f"已寫入 {out.crosscheck_path}")
    return 0


def dry_run(pending: list[PlannedCrosscheck], skipped: int, template: PromptTemplate) -> int:
    print("dry-run：不送出任何請求，也不寫出任何檔案")
    print(f"請求數：{len(pending)}（已有新的紀錄而略過：{skipped}）")
    for judge_id, n in Counter(c.judge.id for c in pending).items():
        chars = sum(len(template.text) + len(c.message) for c in pending if c.judge.id == judge_id)
        print(f"Judge {judge_id}：請求數 {n}、估計輸入字數約 {chars}")
        print_endpoint(next(c.judge for c in pending if c.judge.id == judge_id))
    return 0
