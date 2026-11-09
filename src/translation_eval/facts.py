"""Must-retain fact 的 verdict 判定：同一個 Run、同一個 Component、同一個 Judge 的兩次請求，每條事實的最終 verdict。純計算，沒有檔案。

規則（spec 第 6 節）：
  兩次都有效而且 verdict 相同，是一致，那個 verdict 就是最終 verdict；不同就是 uncertain。
  只有一次有效的 verdict（另一次失敗或作廢），是 uncertain：只有一個意見，不能當作最終結果。
  兩次都沒有有效的 verdict（引用不是逐字子字串等），是無效：事實列入審閱。
  兩個請求都失敗，是 Unjudged；還沒有兩個請求的紀錄，是尚未評完；ground truth 的事實或 anchor 改了，舊紀錄過期。
最終 verdict 都是「一致」的結果；uncertain 與無效要等人手審閱才有最終 verdict。
"""
from dataclasses import dataclass
from enum import StrEnum

from .consistency import Status
from .ground_truth import Sentence, input_hash
from .schemas import JudgeRecord, Outcome, Verdict, WeakeningType


class Pending(StrEnum):
    """一條事實還沒有最終 verdict 的原因：Gate 的「未完成」與 Judge 實驗都用它。值與 FactStatus 的 UNCERTAIN、INVALID 相同。"""
    UNCERTAIN = "uncertain"
    INVALID = "invalid"
    UNJUDGED = "unjudged"
    INCOMPLETE = "incomplete"   # 還沒有兩個請求的紀錄
    STALE = "stale"             # ground truth 改了，舊紀錄過期

    @property
    def label(self) -> str:
        return {"uncertain": "uncertain", "invalid": "無效", "unjudged": "Unjudged", "incomplete": "尚未評完", "stale": "過期"}[self.value]


class FactStatus(StrEnum):
    FINAL = "final"
    UNCERTAIN = "uncertain"
    INVALID = "invalid"


@dataclass(frozen=True)
class FactResult:
    status: FactStatus
    verdict: Verdict | None = None            # 只有 final 有
    weakening_type: WeakeningType | None = None   # 兩次的類型一樣才有
    amplification: int = 0                     # 有效的 verdict 裡，有幾次標了 Amplification（兩次一致才是 2）


@dataclass(frozen=True)
class FactJudgment:
    status: Status
    facts: dict[str, FactResult]   # 事實 key → 結果；只有 JUDGED 才有
    failed: list[Outcome]          # 沒有成功的請求的結果類別
    stale: bool = False            # INCOMPLETE 的原因是 ground truth 改了，舊紀錄過期
    calls: tuple[JudgeRecord, ...] = ()   # 沒有過期的請求紀錄，依請求序號；審閱清單顯示每次的 verdict 用


def _result(valid) -> FactResult:
    if not valid:
        return FactResult(FactStatus.INVALID)
    if len(valid) == 2 and valid[0].verdict == valid[1].verdict:
        same_type = valid[0].weakening_type == valid[1].weakening_type
        amplified = sum(1 for v in valid if v.weakening_type is WeakeningType.AMPLIFICATION)
        return FactResult(FactStatus.FINAL, valid[0].verdict, valid[0].weakening_type if same_type else None, amplified)
    return FactResult(FactStatus.UNCERTAIN)


def judge_facts(calls: list[JudgeRecord], sentences: list[Sentence]) -> FactJudgment:
    """calls：這個 Component 的請求紀錄（最多兩個）；sentences：這個 Component 內的 Sensitive sentence。"""
    current = input_hash(sentences)
    fresh = {c.call_index: c for c in calls if c.input_hash == current}
    if len(fresh) < 2:
        return FactJudgment(Status.INCOMPLETE, {}, [], stale=len(fresh) < len(calls))
    ordered = tuple(sorted(fresh.values(), key=lambda c: c.call_index))
    succeeded = [c for c in fresh.values() if c.outcome is Outcome.OK]
    failed = [c.outcome for c in fresh.values() if c.outcome is not Outcome.OK]
    if not succeeded:
        return FactJudgment(Status.UNJUDGED, {}, failed, calls=ordered)
    facts = {f.key: _result([v for c in succeeded for v in c.verdicts if v.fact == f.key]) for s in sentences for f in s.facts}
    return FactJudgment(Status.JUDGED, facts, failed, calls=ordered)
