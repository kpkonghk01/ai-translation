"""全文篩查兩次結果的一致性：同一個 Run、同一個 Judge 的兩個請求，哪些條目是同一處、哪些要標為 uncertain。純計算，沒有檔案。

規則與 MQM 的 Finding 相同（spec 第 6 節）：兩次的條目若同一個 verdict、譯文片段在譯文裡重疊（沒有譯文片段的 Absent，看原文片段在原文裡是否重疊），
就是同一處，記為一致；配對不到的標為 uncertain。只有一次成功時，那次的條目全部是 uncertain；兩次都失敗是 Unjudged，並記下原因；
還沒有兩個請求的紀錄的，是「尚未評完」。篩查的結果只進審閱清單，不決定 Gate，也不計分。
"""
from dataclasses import dataclass

from .consistency import Status
from .pairs import ComponentPair
from .schemas import JudgeCall, Outcome, ScreenItem
from .textrules import squash

SEPARATOR = "\x00"   # 不是空白：squash 之後兩個 Component 的文字接起來，片段也不會跨過邊界配對


@dataclass(frozen=True)
class MergedScreen:
    item: ScreenItem
    consistent: bool


@dataclass(frozen=True)
class ScreenJudgment:
    status: Status
    items: list[MergedScreen]
    failed: list[Outcome]   # 沒有成功的請求的結果類別


def _interval(full: str, span: str) -> tuple[int, int] | None:
    wanted = squash(span)
    start = full.find(wanted) if wanted else -1
    return None if start < 0 else (start, start + len(wanted))


def _same(a: ScreenItem, b: ScreenItem, source: str, target: str) -> bool:
    if a.verdict != b.verdict:
        return False
    a_target, b_target = bool(a.target_span.strip()), bool(b.target_span.strip())
    if a_target and b_target:
        full, x, y = target, a.target_span, b.target_span
    elif not a_target and not b_target:
        full, x, y = source, a.source_span, b.source_span
    else:
        return False
    first, second = _interval(full, x), _interval(full, y)
    return first is not None and second is not None and first[0] < second[1] and second[0] < first[1]


def judge_screening(calls: list[JudgeCall], pairs: list[ComponentPair]) -> ScreenJudgment:
    by_index = {c.call_index: c for c in calls}
    if len(by_index) < 2:
        return ScreenJudgment(Status.INCOMPLETE, [], [])
    first, second = by_index[1], by_index[2]
    succeeded = [c for c in (first, second) if c.outcome is Outcome.OK]
    failed = [c.outcome for c in (first, second) if c.outcome is not Outcome.OK]
    if not succeeded:
        return ScreenJudgment(Status.UNJUDGED, [], failed)
    if len(succeeded) == 1:
        return ScreenJudgment(Status.JUDGED, [MergedScreen(i, consistent=False) for i in succeeded[0].screenings], failed)
    source = SEPARATOR.join(squash(p.source_text) for p in pairs)
    target = SEPARATOR.join(squash(p.translated_text) for p in pairs if p.translated_text)
    unmatched, merged = list(second.screenings), []
    for a in first.screenings:
        partner = next((b for b in unmatched if _same(a, b, source, target)), None)
        if partner is None:
            merged.append(MergedScreen(a, consistent=False))
        else:
            unmatched.remove(partner)
            merged.append(MergedScreen(a, consistent=True))
    merged.extend(MergedScreen(b, consistent=False) for b in unmatched)
    return ScreenJudgment(Status.JUDGED, merged, failed)
