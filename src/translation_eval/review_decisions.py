"""審閱決定：讀入、對照審閱項目驗證，並提供 Gate 與計分套用決定的查詢。

決定檔（`review-decisions.json`，在實驗的輸出目錄）以項目 key 為索引，附上抄自審閱清單的 input_hash：
key 不存在（輸入改變、重新判過，或抄錯），或 hash 已經改變（過期），指令就失敗並列出這些決定，不靜默套用。
各種項目能寫的決定：
  事實：accept（確認，只用於已有最終 verdict 的：Gate 失敗或通過的）、override 並寫 verdict（uncertain、無效、Unjudged 的要這樣解決）；
  Finding（Judge 的與 Deterministic 的）：accept（uncertain 的因此算作確認）、reject（不計分）、override 並寫 severity；
  其他（無效的輸出、Unjudged 的請求、警示、全文篩查的條目、不計分的 Fidelity-of-force Finding）：accept 或 reject，只表示看過了；
  程式證明的結果（譯文缺少 Component 的事實、結構性的 Deterministic Finding）：只能 accept。
原始的 Judge 輸出不會被改動：決定只在 report 計算時套用。
"""
from dataclasses import dataclass, field

from .errors import InputError
from .loading import read_model
from .review_items import FINAL_FACT_STATUSES, ItemKind, ItemStatus, ReviewItem, check_finding_keys
from .schemas import ComponentResult, ReviewAction, ReviewDecision, ReviewDecisionFile, RunKey
from .store import OutputDir

FINDING_KINDS = (ItemKind.FINDING, ItemKind.CHECK_FINDING)


@dataclass(frozen=True)
class Decisions:
    by_key: dict[str, ReviewDecision] = field(default_factory=dict)

    def get(self, key: str) -> ReviewDecision | None:
        return self.by_key.get(key)

    def __len__(self) -> int:
        return len(self.by_key)

    def action_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for decision in self.by_key.values():
            counts[decision.action.value] = counts.get(decision.action.value, 0) + 1
        return counts


def load_decisions(out: OutputDir) -> Decisions:
    if not out.decisions_path.exists():
        return Decisions()
    file = read_model(str(out.decisions_path), ReviewDecisionFile)
    keys = [d.key for d in file.decisions]
    repeated = sorted({k for k in keys if keys.count(k) > 1})
    if repeated:
        raise InputError([f"{out.decisions_path}：同一個項目有不只一條決定：{key}" for key in repeated])
    return Decisions({d.key: d for d in file.decisions})


def _rule_problem(decision: ReviewDecision, item: ReviewItem) -> str | None:
    action = decision.action
    if item.locked and action is not ReviewAction.ACCEPT:
        return "這是程式證明的結果（譯文缺少 Component，或結構性的 Finding），只能 accept（表示看過了）"
    if item.kind is ItemKind.FINDING and item.status is ItemStatus.SCREENED and action is ReviewAction.OVERRIDE:
        return "Fidelity-of-force 不計分，沒有 Severity 可以改判：只能 accept 或 reject（表示看過了）"
    if item.kind is ItemKind.FACT:
        if action is ReviewAction.REJECT:
            return "事實不能駁回：要改判就寫 override 並指定 verdict"
        if action is ReviewAction.ACCEPT and item.status not in FINAL_FACT_STATUSES:
            return f"這個事實的狀態是 {item.status.value}，沒有最終 verdict 可以接受：要寫 override 並指定 verdict"
        if action is ReviewAction.OVERRIDE and decision.verdict is None:
            return "改判事實要寫 verdict"
    elif item.kind in FINDING_KINDS:
        if action is ReviewAction.OVERRIDE and decision.severity is None:
            return "改判 Finding 要寫 severity"
    elif action is ReviewAction.OVERRIDE:
        return "這種項目只能 accept 或 reject（表示看過了）"
    return None


def check_decisions(decisions: Decisions, items: list[ReviewItem]) -> None:
    """決定指向不存在的項目、輸入已經改變，或寫法不適用於那種項目：失敗並列出（只列 key，不列內容）。"""
    by_key = {item.key: item for item in items}
    problems = []
    for key, decision in decisions.by_key.items():
        item = by_key.get(key)
        if item is None:
            problems.append(f"決定 {key}：項目不存在（輸入改變、重新判過，或 key 抄錯）")
        elif decision.input_hash != item.input_hash:
            problems.append(f"決定 {key}：輸入的 hash 已經改變（過期），要重新審閱這個項目")
        elif (rule := _rule_problem(decision, item)) is not None:
            problems.append(f"決定 {key}：{rule}")
    if problems:
        raise InputError(problems)


def apply_check_decisions(rows: list[ComponentResult], decisions: Decisions) -> list[ComponentResult]:
    """Deterministic Finding 除非被審閱駁回，否則一律計入：駁回的從結果拿掉，改判 Severity 的換成審閱者判的，
    之後的計數與分數都以這份結果為準。"""
    out = []
    for row in rows:
        findings = []
        for key, finding in zip(check_finding_keys(RunKey.of(row), row.component_id, row.findings), row.findings):
            decision = decisions.get(key)
            if decision is not None and decision.action is ReviewAction.REJECT:
                continue
            if decision is not None and decision.severity is not None:
                finding = finding.model_copy(update={"severity": decision.severity})
            findings.append(finding)
        out.append(row if findings == row.findings else row.model_copy(update={"findings": findings}))
    return out
