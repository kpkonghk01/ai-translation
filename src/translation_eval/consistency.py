"""Judge 兩次結果的一致性：同一個 Component 的兩個請求，哪些 Finding 是同一個、哪些要標為 uncertain。純計算，沒有檔案。

規則（spec 第 6 節）：
  兩次的 Finding 若同一 Dimension、譯文片段重疊（譯文片段都是空的時，看原文片段是否重疊），就是同一個 Finding，記為一致；
  Severity 不同時取較高者，並標記 Severity 不一致。配對不到的 Finding 標為 uncertain。
  只有一次成功時，那次的 Finding 全部標為 uncertain；兩次都失敗是 Unjudged，並記下原因。
  還沒有兩個請求的紀錄的，是「尚未評完」，不是 Unjudged。
"""
from dataclasses import dataclass
from enum import StrEnum

from .pairs import ComponentPair
from .schemas import JudgeCall, JudgeFinding, Outcome, Severity, SourceDefect
from .textrules import squash

SEVERITY_ORDER = {Severity.MINOR: 0, Severity.MAJOR: 1, Severity.CRITICAL: 2}


class Status(StrEnum):
    JUDGED = "judged"
    UNJUDGED = "unjudged"
    INCOMPLETE = "incomplete"   # 還沒有兩個請求的紀錄


@dataclass(frozen=True)
class MergedFinding:
    finding: JudgeFinding   # 兩次一致的取 Severity 較高的那一個
    consistent: bool
    severity_mismatch: bool


@dataclass(frozen=True)
class Judgment:
    status: Status
    findings: list[MergedFinding]
    failed: list[Outcome]   # 沒有成功的請求的結果類別
    defects: list[SourceDefect]


def _interval(text: str, span: str) -> tuple[int, int] | None:
    squashed, wanted = squash(text), squash(span)
    start = squashed.find(wanted) if wanted else -1
    return None if start < 0 else (start, start + len(wanted))


def same_finding(a: JudgeFinding, b: JudgeFinding, pair: ComponentPair) -> bool:
    """兩個 Finding 是不是同一處：同一 Dimension，譯文片段重疊（譯文片段都是空的就看原文片段）。校準用它對照標註。"""
    if a.dimension != b.dimension:
        return False
    a_target, b_target = bool(a.target_span.strip()), bool(b.target_span.strip())
    if a_target and b_target:
        text, x, y = pair.translated_text or "", a.target_span, b.target_span
    elif not a_target and not b_target:
        text, x, y = pair.source_text, a.source_span, b.source_span
    else:
        return False
    first, second = _interval(text, x), _interval(text, y)
    return first is not None and second is not None and first[0] < second[1] and second[0] < first[1]


def _merge(first: list[JudgeFinding], second: list[JudgeFinding], pair: ComponentPair) -> list[MergedFinding]:
    unmatched, merged = list(second), []
    for a in first:
        partner = next((b for b in unmatched if same_finding(a, b, pair)), None)
        if partner is None:
            merged.append(MergedFinding(a, consistent=False, severity_mismatch=False))
            continue
        unmatched.remove(partner)
        higher = a if SEVERITY_ORDER[a.severity] >= SEVERITY_ORDER[partner.severity] else partner
        merged.append(MergedFinding(higher, consistent=True, severity_mismatch=a.severity != partner.severity))
    merged.extend(MergedFinding(b, consistent=False, severity_mismatch=False) for b in unmatched)
    return merged


def judge_component(calls: list[JudgeCall], pair: ComponentPair) -> Judgment:
    """同一個 Run、同一個 Component、同一個 Judge 的請求紀錄（最多兩個）→ 判定。"""
    by_index = {c.call_index: c for c in calls}
    if len(by_index) < 2:
        return Judgment(Status.INCOMPLETE, [], [], [])
    first, second = by_index[1], by_index[2]
    succeeded = [c for c in (first, second) if c.outcome is Outcome.OK]
    failed = [c.outcome for c in (first, second) if c.outcome is not Outcome.OK]
    defects = [d for c in succeeded for d in c.source_defects]
    if not succeeded:
        return Judgment(Status.UNJUDGED, [], failed, [])
    if len(succeeded) == 1:
        return Judgment(Status.JUDGED, [MergedFinding(f, consistent=False, severity_mismatch=False) for f in succeeded[0].findings], failed, defects)
    return Judgment(Status.JUDGED, _merge(first.findings, second.findings, pair), failed, defects)
