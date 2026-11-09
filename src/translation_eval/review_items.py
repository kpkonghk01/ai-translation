"""審閱項目：審閱者可能要看、可以對它寫決定的每一件事。

種類：事實的 verdict（fact）、Judge 的 MQM Finding（finding）、Deterministic Finding（check-finding）、無效的 Judge 輸出（invalid）、
MQM 沒評成的 Component（unjudged）、Deterministic 警示（warning，含疑似 Refusal）。
必看：Gate 失敗的事實（最終 verdict 是 Weakened 或 Absent，包括漏段）、uncertain 或無效的事實、Unjudged 的事實、uncertain 的 Finding、
無效的 Judge 輸出、Unjudged 的 Component、所有警示。其餘（通過的事實、一致的 Finding、Deterministic Finding）是抽查的候選。
每個項目有穩定的 key（種類加上身分的 hash：Run、Component、Judge 和能指認這一項的內容）和輸入的 hash（原文、譯文和 Judge 的原始輸出）：
輸入改了，key 或 hash 就變，審閱決定隨之過期，不會靜默套用在不同的內容上。只審 role 為 primary 的 Judge 的結果（事實是該分組的主 Judge）。
"""
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from .articles import apply_variant
from .config import Experiment
from .consistency import Status, judge_component
from .facts import FactStatus, judge_facts
from .ground_truth import GroundTruth
from .pairs import component_pairs
from .schemas import ComponentResult, Finding, JudgeFinding, JudgeRole, Outcome, RunKey, RunRecord, Verdict, WarningKind
from .store import OutputDir
from .textrules import squash

FAILING = (Verdict.WEAKENED, Verdict.ABSENT)
MAX_RAW_SHOWN = 300


class ItemKind(StrEnum):
    FACT = "fact"
    FINDING = "finding"
    CHECK_FINDING = "check-finding"
    INVALID = "invalid"
    UNJUDGED = "unjudged"
    WARNING = "warning"


class ItemStatus(StrEnum):
    FAILED = "failed"                      # Gate 失敗的事實
    UNCERTAIN = "uncertain"
    INVALID = "invalid"
    UNJUDGED = "unjudged"
    SUSPECTED_REFUSAL = "suspected-refusal"
    WARNING = "warning"
    PASSED = "passed"                      # 以下是抽查的候選
    CONSISTENT = "consistent"
    CHECKED = "checked"


MUST_SEE = frozenset({ItemStatus.FAILED, ItemStatus.UNCERTAIN, ItemStatus.INVALID, ItemStatus.UNJUDGED,
                      ItemStatus.SUSPECTED_REFUSAL, ItemStatus.WARNING})


@dataclass
class ReviewItem:
    key: str
    kind: ItemKind
    status: ItemStatus
    run: RunKey
    component_id: str
    judge: str | None
    input_hash: str
    source_text: str
    translation_text: str | None
    lines: list[str] = field(default_factory=list)   # Judge 的引用、理由等，一行一項

    @property
    def must_see(self) -> bool:
        return self.status in MUST_SEE


def digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]


def item_key(kind: ItemKind, run: RunKey, component_id: str, judge: str | None, *identity) -> str:
    return f"{kind.value}-{digest(kind.value, run.model_dump(mode='json'), component_id, judge, *identity)}"


def fact_item_key(run: RunKey, component_id: str, judge: str, fact_key: str) -> str:
    return item_key(ItemKind.FACT, run, component_id, judge, fact_key)


def finding_item_key(run: RunKey, component_id: str, judge: str, finding: JudgeFinding) -> str:
    return item_key(ItemKind.FINDING, run, component_id, judge, finding.dimension.value, squash(finding.source_span), squash(finding.target_span))


def check_finding_item_key(run: RunKey, component_id: str, finding: Finding) -> str:
    return item_key(ItemKind.CHECK_FINDING, run, component_id, None, finding.dimension.value, finding.subtype,
                    squash(finding.source_span), squash(finding.target_span))


def _one_line(text: str, limit: int | None = None) -> str:
    """Judge 寫的文字放進清單前壓成一行，才不會長出新的標題或項目。"""
    line = " ".join(text.split())
    return line if limit is None or len(line) <= limit else line[:limit] + "…"


def _call_summary(calls) -> list:
    return [[c.call_index, c.outcome.value, c.reason, [f.model_dump(mode="json") for f in c.findings],
             [v.model_dump(mode="json") for v in c.verdicts], [i.model_dump(mode="json") for i in c.invalid]] for c in calls]


def build_items(experiment: Experiment, out: OutputDir, records: list[RunRecord], rows: list[ComponentResult], articles,
                truth: GroundTruth | None) -> list[ReviewItem]:
    mqm, facts = defaultdict(list), defaultdict(list)
    for call in out.read_judge_calls("mqm"):
        mqm[(call.judge, RunKey.of(call), call.component_id)].append(call)
    for call in out.read_judge_calls("fact"):
        facts[(call.judge, RunKey.of(call), call.component_id)].append(call)
    rows_by_run = defaultdict(list)
    for row in rows:
        rows_by_run[RunKey.of(row)].append(row)
    primary = [j for j in experiment.judges if j.config.role is JudgeRole.PRIMARY]
    items = []
    for record in (r for r in records if r.outcome is Outcome.OK):
        run = RunKey.of(record)
        pairs = component_pairs(apply_variant(articles[record.article_id], record.input_variant), out.read_translation(record))
        by_id = {p.id: p for p in pairs}

        def make(kind, status, component_id, judge, identity, hash_parts, lines):
            pair = by_id.get(component_id)
            source, target = (pair.source_text, pair.translated_text) if pair else ("（原文沒有這個 Component）", None)
            items.append(ReviewItem(item_key(kind, run, component_id, judge, *identity), kind, status, run, component_id, judge,
                                    digest(source, target, *hash_parts), source, target, lines))

        for row in rows_by_run.get(run, []):
            for finding in row.findings:
                make(ItemKind.CHECK_FINDING, ItemStatus.CHECKED, row.component_id, None,
                     (finding.dimension.value, finding.subtype, squash(finding.source_span), squash(finding.target_span)), (finding.model_dump(mode="json"),),
                     [f"{finding.dimension.value}／{finding.subtype}／{finding.severity.value}：{_one_line(finding.reason)}"])
            for warning in row.warnings:
                status = ItemStatus.SUSPECTED_REFUSAL if warning.kind is WarningKind.SUSPECTED_REFUSAL else ItemStatus.WARNING
                make(ItemKind.WARNING, status, row.component_id, None, (warning.kind.value, warning.detail), (warning.model_dump(mode="json"),),
                     [f"{warning.kind.value}：{_one_line(warning.detail)}"])
        for judge in primary:
            for pair in pairs:
                if pair.translated_text is not None:
                    _mqm_items(items, run, pair, judge.id, mqm.get((judge.id, run, pair.id), []))
        if truth is not None:
            _fact_items(items, experiment, truth, record, by_id, facts)
    return items


def _mqm_items(items, run: RunKey, pair, judge: str, calls) -> None:
    judgment = judge_component(calls, pair)
    if judgment.status is Status.INCOMPLETE:
        return
    base = dict(run=run, component_id=pair.id, judge=judge, source_text=pair.source_text, translation_text=pair.translated_text)
    summary = _call_summary(sorted(calls, key=lambda c: c.call_index))
    if judgment.status is Status.UNJUDGED:
        reasons = "、".join(f"第 {c.call_index} 次 {c.outcome.value}" + (f"（{_one_line(c.reason)}）" if c.reason else "") for c in sorted(calls, key=lambda c: c.call_index))
        items.append(ReviewItem(key=item_key(ItemKind.UNJUDGED, run, pair.id, judge, "mqm"), kind=ItemKind.UNJUDGED, status=ItemStatus.UNJUDGED,
                                input_hash=digest(pair.source_text, pair.translated_text, summary), lines=[f"MQM 的兩次請求都失敗：{reasons}"], **base))
        return
    for merged in judgment.findings:
        f = merged.finding
        status = ItemStatus.CONSISTENT if merged.consistent else ItemStatus.UNCERTAIN
        note = "兩次一致" if merged.consistent else "只有一次提出，或兩次沒有配對"
        if merged.severity_mismatch:
            note += "；兩次的 Severity 不同，取較高者"
        items.append(ReviewItem(
            key=finding_item_key(run, pair.id, judge, f), kind=ItemKind.FINDING, status=status,
            input_hash=digest(pair.source_text, pair.translated_text, summary),
            lines=[f"{f.dimension.value}／{f.subtype}／{f.severity.value}（{note}）：{_one_line(f.reason)}",
                   f"原文片段：「{_one_line(f.source_span)}」；譯文片段：「{_one_line(f.target_span)}」"], **base))
    for call in sorted(calls, key=lambda c: c.call_index):
        for invalid in call.invalid:
            if invalid.kind == "verdict":
                continue
            items.append(ReviewItem(
                key=item_key(ItemKind.INVALID, run, pair.id, judge, "mqm", call.call_index, digest(invalid.raw)), kind=ItemKind.INVALID,
                status=ItemStatus.INVALID, input_hash=digest(pair.source_text, pair.translated_text, invalid.model_dump(mode="json")),
                lines=[f"第 {call.call_index} 次的 {invalid.kind} 無效：{invalid.why}", f"Judge 的輸出：{_one_line(invalid.raw, MAX_RAW_SHOWN)}"], **base))


def _fact_lines(judgment, fact) -> list[str]:
    lines = []
    for call in judgment.calls:
        verdict = next((v for v in call.verdicts if v.fact == fact.key), None)
        if call.outcome is not Outcome.OK:
            lines.append(f"Judge 第 {call.call_index} 次：請求失敗（{call.outcome.value}）")
        elif verdict is None:
            why = next((i.why for i in call.invalid if i.fact == fact.key), "沒有 verdict")
            lines.append(f"Judge 第 {call.call_index} 次：verdict 作廢（{why}）")
        else:
            kind = f"（{verdict.weakening_type.value}）" if verdict.weakening_type else ""
            quote = f"；引用「{_one_line(verdict.quote)}」" if verdict.quote else ""
            lines.append(f"Judge 第 {call.call_index} 次：{verdict.verdict.value}{kind}{quote}；理由：{_one_line(verdict.reason)}")
    return lines


def _fact_items(items, experiment: Experiment, truth: GroundTruth, record: RunRecord, by_id, facts) -> None:
    run = RunKey.of(record)
    if record.article_id not in truth.domains:
        return
    judge, _ = experiment.primary_judge_for(truth.group_of(record.article_id))
    if judge is None:
        return
    for component_id, pair in by_id.items():
        sentences = truth.sentences_in(record.article_id, component_id)
        if not sentences:
            continue
        judgment = None if pair.translated_text is None else judge_facts(facts.get((judge.id, run, component_id), []), sentences)
        if judgment is not None and judgment.status is Status.INCOMPLETE:
            continue
        for sentence in sentences:
            for fact in sentence.facts:
                prefix = f"事實（{fact.kind.value}）：{_one_line(fact.text)}"
                if judgment is None:
                    status, lines = ItemStatus.FAILED, [prefix, "譯文缺少這個 Component：事實直接判 Absent，沒有送給 Judge"]
                elif judgment.status is Status.UNJUDGED:
                    status, lines = ItemStatus.UNJUDGED, [prefix, *_fact_lines(judgment, fact)]
                else:
                    result = judgment.facts[fact.key]
                    status = {FactStatus.UNCERTAIN: ItemStatus.UNCERTAIN, FactStatus.INVALID: ItemStatus.INVALID}.get(
                        result.status, ItemStatus.FAILED if result.verdict in FAILING else ItemStatus.PASSED)
                    lines = [prefix, *_fact_lines(judgment, fact)]
                calls = [] if judgment is None else _call_summary(judgment.calls)
                items.append(ReviewItem(
                    key=fact_item_key(run, component_id, judge.id, fact.key), kind=ItemKind.FACT, status=status, run=run, component_id=component_id,
                    judge=judge.id, input_hash=digest(pair.source_text, pair.translated_text, fact.kind.value, fact.text, calls),
                    source_text=pair.source_text, translation_text=pair.translated_text, lines=lines))
