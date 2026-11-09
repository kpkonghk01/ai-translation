"""審閱項目：審閱者可能要看、可以對它寫決定的每一件事。

種類：事實的 verdict（fact）、Judge 的 MQM Finding（finding）、Deterministic Finding（check-finding）、無效的 Judge 輸出（invalid）、
MQM 沒評成的 Component（unjudged）、Deterministic 警示（warning，含疑似 Refusal）。
必看：Gate 失敗的事實（最終 verdict 是 Weakened 或 Absent，包括漏段）、uncertain 或無效的事實、Unjudged 的事實、uncertain 的 Finding、
無效的 Judge 輸出、Unjudged 的 Component、所有警示。其餘（通過的事實、一致的 Finding、Deterministic Finding）是抽查的候選。
每個項目有穩定的 key（種類加上身分的 hash：Run、Component、Judge 和能指認這一項的內容；同一個 Component 內身分一樣的項目再加序號）
和輸入的 hash（原文、譯文和 Judge 的原始輸出）：輸入改了，key 或 hash 就變，審閱決定隨之過期，不會靜默套用在不同的內容上。
只審 role 為 primary 的 Judge 的結果（事實是該分組的主 Judge）；mqm_run_limit 刻意沒評的 Run 沒有 MQM 項目。
程式證明的結果不能改判：譯文缺少 Component 而直接判 Absent 的事實，和結構性的 Deterministic Finding（漏段、多出、重覆、type 被改），
只能 accept（看過了）。
"""
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from .articles import apply_variant
from .config import Experiment
from .consistency import Status, judge_component
from .facts import FAILING_VERDICTS, FactStatus, judge_facts
from .ground_truth import GroundTruth
from .pairs import component_pairs
from .schemas import (DUPLICATE_COMPONENT, EXTRA_COMPONENT, OMISSION, TYPE_CHANGED, CheckWarning, ComponentResult, Finding, JudgeFinding,
                      JudgeRole, Outcome, RunKey, RunRecord, WarningKind)
from .store import OutputDir
from .textrules import squash

MAX_RAW_SHOWN = 300
STRUCTURAL_SUBTYPES = frozenset({OMISSION, EXTRA_COMPONENT, DUPLICATE_COMPONENT, TYPE_CHANGED})   # 程式證明的，不能駁回或改判


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
FINAL_FACT_STATUSES = (ItemStatus.FAILED, ItemStatus.PASSED)   # 已有最終 verdict 的事實


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
    locked: bool = False                              # 程式證明的結果：只能 accept

    @property
    def must_see(self) -> bool:
        return self.status in MUST_SEE


def digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]


def item_key(kind: ItemKind, run: RunKey, component_id: str, judge: str | None, *identity) -> str:
    return f"{kind.value}-{digest(kind.value, run.model_dump(mode='json'), component_id, judge, *identity)}"


def _with_ordinals(identities: list[tuple]) -> list[tuple]:
    """同一個 Component 內身分一樣的項目（例如兩處一樣的殘留漢字）各加一個序號，key 才不會撞。"""
    seen: Counter = Counter()
    out = []
    for identity in identities:
        out.append((*identity, seen[identity]))
        seen[identity] += 1
    return out


def fact_item_key(run: RunKey, component_id: str, judge: str, fact_key: str) -> str:
    return item_key(ItemKind.FACT, run, component_id, judge, fact_key)


def finding_item_keys(run: RunKey, component_id: str, judge: str, findings: list[JudgeFinding]) -> list[str]:
    """一個 Component 的（合併後的）Judge Finding，依序的 key。"""
    identities = [(f.dimension.value, squash(f.source_span), squash(f.target_span)) for f in findings]
    return [item_key(ItemKind.FINDING, run, component_id, judge, *i) for i in _with_ordinals(identities)]


def check_finding_keys(run: RunKey, component_id: str, findings: list[Finding]) -> list[str]:
    """一個 Component 的 Deterministic Finding，依序的 key。"""
    identities = [(f.dimension.value, f.subtype, squash(f.source_span), squash(f.target_span)) for f in findings]
    return [item_key(ItemKind.CHECK_FINDING, run, component_id, None, *i) for i in _with_ordinals(identities)]


def warning_item_key(run: RunKey, component_id: str, warning: CheckWarning) -> str:
    return item_key(ItemKind.WARNING, run, component_id, None, warning.kind.value, warning.detail)


def unjudged_item_key(run: RunKey, component_id: str, judge: str) -> str:
    return item_key(ItemKind.UNJUDGED, run, component_id, judge, "mqm")


def invalid_item_key(run: RunKey, component_id: str, judge: str, task: str, call_index: int, raw: str) -> str:
    return item_key(ItemKind.INVALID, run, component_id, judge, task, call_index, digest(raw))


def is_structural(finding: Finding) -> bool:
    return finding.subtype in STRUCTURAL_SUBTYPES


def _one_line(text: str, limit: int | None = None) -> str:
    """Judge 寫的文字放進清單前壓成一行，才不會長出新的標題或項目。"""
    line = " ".join(text.split())
    return line if limit is None or len(line) <= limit else line[:limit] + "…"


def _raw(component) -> str:
    return json.dumps(component.model_dump(mode="json"), ensure_ascii=False)


def _call_summary(calls) -> list:
    return [[c.call_index, c.outcome.value, c.reason, [f.model_dump(mode="json") for f in c.findings],
             [v.model_dump(mode="json") for v in c.verdicts], [i.model_dump(mode="json") for i in c.invalid]] for c in calls]


def _item(key, kind, status, run, component_id, judge, source, target, hash_parts, lines, locked=False) -> ReviewItem:
    """input_hash 涵蓋顯示給審閱者的原文與譯文，加上 hash_parts（Judge 的原始輸出、事實等）。"""
    return ReviewItem(key, kind, status, run, component_id, judge, digest(source, target, *hash_parts), source, target, lines, locked)


def build_items(experiment: Experiment, out: OutputDir, records: list[RunRecord], rows: list[ComponentResult], articles,
                truth: GroundTruth | None) -> list[ReviewItem]:
    calls = {task: defaultdict(list) for task in ("mqm", "fact")}
    for task, grouped in calls.items():
        for call in out.read_judge_calls(task):
            grouped[(call.judge, RunKey.of(call), call.component_id)].append(call)
    rows_by_run = defaultdict(list)
    for row in rows:
        rows_by_run[RunKey.of(row)].append(row)
    primary = [j for j in experiment.judges if j.config.role is JudgeRole.PRIMARY]
    limits = experiment.config.judging.mqm_run_limit if experiment.config.judging else {}
    items = []
    for record in (r for r in records if r.outcome is Outcome.OK):
        run = RunKey.of(record)
        article = apply_variant(articles[record.article_id], record.input_variant)
        translation = out.read_translation(record)
        pairs = component_pairs(article, translation)
        by_id = {p.id: p for p in pairs}
        source_components = {c.id: c for c in reversed(article.components)}   # 重覆的 id 取第一個
        translated_components = {c.id: c for c in reversed(translation.components)}

        def texts(component_id: str):
            """Component 的原文與譯文：要翻譯的用文字；不翻譯的、譯文多出來的沒有文字，顯示整個 Component。"""
            if component_id in by_id:
                return by_id[component_id].source_text, by_id[component_id].translated_text
            source, target = source_components.get(component_id), translated_components.get(component_id)
            return ("（原文沒有這個 Component）" if source is None else _raw(source)), (None if target is None else _raw(target))

        for row in rows_by_run.get(run, []):
            source, target = texts(row.component_id)
            for key, finding in zip(check_finding_keys(run, row.component_id, row.findings), row.findings):
                items.append(_item(key, ItemKind.CHECK_FINDING, ItemStatus.CHECKED, run, row.component_id, None, source, target,
                                   (finding.model_dump(mode="json"),),
                                   [f"{finding.dimension.value}／{finding.subtype}／{finding.severity.value}：{_one_line(finding.reason)}"],
                                   locked=is_structural(finding)))
            for warning in row.warnings:
                status = ItemStatus.SUSPECTED_REFUSAL if warning.kind is WarningKind.SUSPECTED_REFUSAL else ItemStatus.WARNING
                items.append(_item(warning_item_key(run, row.component_id, warning), ItemKind.WARNING, status, run, row.component_id, None,
                                   source, target, (warning.model_dump(mode="json"),), [f"{warning.kind.value}：{_one_line(warning.detail)}"]))
        limit = limits.get(record.approach)
        if limit is None or record.run_index <= limit:   # mqm_run_limit 刻意沒評的 Run 沒有 MQM 項目
            for judge in primary:
                for pair in pairs:
                    if pair.translated_text is not None:
                        _mqm_items(items, run, pair, judge.id, calls["mqm"].get((judge.id, run, pair.id), []))
        if truth is not None:
            _fact_items(items, experiment, truth, record, by_id, calls["fact"])
    return items


def _mqm_items(items, run: RunKey, pair, judge: str, calls) -> None:
    judgment = judge_component(calls, pair)
    if judgment.status is Status.INCOMPLETE:
        return
    ordered = sorted(calls, key=lambda c: c.call_index)
    summary = _call_summary(ordered)
    if judgment.status is Status.UNJUDGED:
        reasons = "、".join(f"第 {c.call_index} 次 {c.outcome.value}" + (f"（{_one_line(c.reason)}）" if c.reason else "") for c in ordered)
        items.append(_item(unjudged_item_key(run, pair.id, judge), ItemKind.UNJUDGED, ItemStatus.UNJUDGED, run, pair.id, judge,
                           pair.source_text, pair.translated_text, (summary,), [f"MQM 的兩次請求都失敗：{reasons}"]))
        return
    for key, merged in zip(finding_item_keys(run, pair.id, judge, [m.finding for m in judgment.findings]), judgment.findings):
        f = merged.finding
        note = "兩次一致" if merged.consistent else "只有一次提出，或兩次沒有配對"
        if merged.severity_mismatch:
            note += "；兩次的 Severity 不同，取較高者"
        items.append(_item(key, ItemKind.FINDING, ItemStatus.CONSISTENT if merged.consistent else ItemStatus.UNCERTAIN, run, pair.id, judge,
                           pair.source_text, pair.translated_text, (summary,),
                           [f"{f.dimension.value}／{f.subtype}／{f.severity.value}（{note}）：{_one_line(f.reason)}",
                            f"原文片段：「{_one_line(f.source_span)}」；譯文片段：「{_one_line(f.target_span)}」"]))
    for call in ordered:
        for invalid in (i for i in call.invalid if i.kind != "verdict"):   # verdict 的在事實項目裡
            items.append(_item(invalid_item_key(run, pair.id, judge, "mqm", call.call_index, invalid.raw), ItemKind.INVALID, ItemStatus.INVALID,
                               run, pair.id, judge, pair.source_text, pair.translated_text, (invalid.model_dump(mode="json"),),
                               [f"第 {call.call_index} 次的 {invalid.kind} 無效：{invalid.why}", f"Judge 的輸出：{_one_line(invalid.raw, MAX_RAW_SHOWN)}"]))


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


def _fact_items(items, experiment: Experiment, truth: GroundTruth, record: RunRecord, by_id, fact_calls) -> None:
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
        judgment = None if pair.translated_text is None else judge_facts(fact_calls.get((judge.id, run, component_id), []), sentences)
        if judgment is not None and judgment.status is Status.INCOMPLETE:
            continue
        summary = [] if judgment is None else _call_summary(judgment.calls)
        for sentence in sentences:
            for fact in sentence.facts:
                prefix = f"事實（{fact.kind.value}）：{_one_line(fact.text)}"
                locked = judgment is None
                if judgment is None:
                    status, lines = ItemStatus.FAILED, [prefix, "譯文缺少這個 Component：事實直接判 Absent，沒有送給 Judge"]
                elif judgment.status is Status.UNJUDGED:
                    status, lines = ItemStatus.UNJUDGED, [prefix, *_fact_lines(judgment, fact)]
                else:
                    result = judgment.facts[fact.key]
                    status = {FactStatus.UNCERTAIN: ItemStatus.UNCERTAIN, FactStatus.INVALID: ItemStatus.INVALID}.get(
                        result.status, ItemStatus.FAILED if result.verdict in FAILING_VERDICTS else ItemStatus.PASSED)
                    lines = [prefix, *_fact_lines(judgment, fact)]
                items.append(_item(fact_item_key(run, component_id, judge.id, fact.key), ItemKind.FACT, status, run, component_id, judge.id,
                                   pair.source_text, pair.translated_text, (fact.kind.value, fact.text, summary), lines, locked=locked))
        for call in (judgment.calls if judgment is not None else ()):
            for invalid in (i for i in call.invalid if i.fact is None):   # 不認識的 fact_id、不是物件的條目：不屬於任何一條事實
                items.append(_item(invalid_item_key(run, component_id, judge.id, "fact", call.call_index, invalid.raw), ItemKind.INVALID, ItemStatus.INVALID,
                                   run, component_id, judge.id, pair.source_text, pair.translated_text, (invalid.model_dump(mode="json"),),
                                   [f"第 {call.call_index} 次的 verdict 無效：{invalid.why}", f"Judge 的輸出：{_one_line(invalid.raw, MAX_RAW_SHOWN)}"]))
