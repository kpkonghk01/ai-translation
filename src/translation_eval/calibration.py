"""Judge 校準的計算：Gold set 的項目配上某個 Judge 的請求紀錄，算出各項指標。純計算，沒有檔案；怎樣寫成報告在 calibration_report.py。

只用「新的」請求紀錄：prompt、Judge 設定、項目送給 Judge 的內容（含它指向的事實）任何一項改了，舊的紀錄就過期、忽略，並計數。所以 prompt 一改，
recall 就回到「未量度」，直到重新校準：舊 prompt 量到的 recall 說不了新 prompt。預期答案與備註沒有送給 Judge，改了不用重送。
配對規則與 Judge 兩次結果的一致性相同（consistency.same_finding）：同一 Dimension、譯文片段重疊（譯文片段是空的就看原文片段）。

指標的定義（分母都寫在報告裡）：
  - 預期 Finding 的 recall：預期的 Finding 被 Judge 的 Finding 對上的比例，只計一致的與連 uncertain 一起計各一個；對上的之中 Severity 一樣的比例另計。
    分母是跑完（兩個請求都有結果）的項目的預期 Finding；Judge 被擋而 Unjudged 的項目，預期的 Finding 算沒有抓到（留在分母）：被擋的 Judge
    什麼也抓不到，不能因此讓 recall 好看。還沒有兩個請求的項目是「尚未評完」，不在分母，另外列。
  - 驗證後 precision：只看 annotated 項目（標註是完整的）。Judge 的有效 Finding（逐字驗證通過、兩次合併之後）之中，對得上預期 Finding 的比例。
    無效的 Finding 不在內，在「無效 Finding 率」。variant 的 Judge Finding 沒有人逐條標過，不計入 precision。clean 項目的 Finding 一律是誤報，
    在「clean 項目上的誤報」，不在 precision。
  - clean 項目上的誤報：clean 項目之中，Judge 報了任何 Finding 的項目數。
  - Finding 的錯誤率：annotated 與 clean 項目（標註完整的）上，一致的 Finding 與 uncertain 的 Finding 各有多少對不上預期。用來檢驗「跑兩次」：
    若 uncertain 的錯誤率沒有比較高，兩次的不一致就沒有提供資訊。
  - 事實：只看項目列出預期 verdict 的事實。有最終 verdict（兩次一致）的才算準確與否；uncertain、無效另計；Unjudged 的項目，預期的事實沒有最終
    verdict，也留在分母。Weakened／Absent 的 recall 是預期為該 verdict 的事實之中，Judge 的最終 verdict 正是它的比例；「失敗的事實」的 recall
    （Gate 旁邊列的）是預期 Weakened 或 Absent 的事實之中，最終 verdict 是 Weakened 或 Absent 的比例，uncertain 與 Unjudged 算沒有抓到。
    預期 Present 而最終 verdict 是 Weakened 或 Absent 的，是 Gate 的誤報。
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .config import PlannedJudge
from .consistency import Judgment, Status, judge_component, same_finding
from .facts import FactJudgment, FactStatus, judge_facts
from .gold import GoldSet, item_hash, item_pair, item_sentences
from .ground_truth import GroundTruth, fact_key
from .judging import task_templates
from .pairs import ComponentPair
from .schemas import FAILING_VERDICTS, GoldCall, GoldCategory, GoldItem, InvalidKind, JudgeFinding, Task

COMPLETE_ANNOTATION = (GoldCategory.ANNOTATED, GoldCategory.CLEAN)   # 標註是完整的：沒標到的 Finding 就是多報


@dataclass(frozen=True)
class ItemOutcome:
    item: GoldItem
    pair: ComponentPair
    mqm: Judgment
    facts: FactJudgment | None   # 項目沒有 facts 時是 None
    valid_findings: int          # 兩次請求裡通過驗證的 Finding 數（合併之前）
    invalid_findings: int


@dataclass(frozen=True)
class JudgeEvaluation:
    judge: PlannedJudge
    outcomes: list[ItemOutcome]
    requests: Counter   # task → 新的請求數
    stale: int          # 過期而忽略的請求數

    @property
    def has_records(self) -> bool:
        """有校準的請求（新的或過期的）。沒有就是還沒校準。"""
        return bool(self.requests or self.stale)


@dataclass
class Metrics:
    """一組項目的計數。報告的每個比率是其中兩個數。"""
    items: int = 0
    mqm_judged: int = 0
    mqm_unjudged: int = 0
    mqm_incomplete: int = 0
    expected: int = 0
    found_consistent: int = 0
    found_any: int = 0
    severity_pairs: int = 0
    severity_agree: int = 0
    annotated_findings: int = 0
    annotated_correct: int = 0
    consistent_findings: int = 0
    consistent_wrong: int = 0
    uncertain_findings: int = 0
    uncertain_wrong: int = 0
    clean_items: int = 0
    clean_flagged_consistent: int = 0
    clean_flagged_any: int = 0
    valid_findings: int = 0
    invalid_findings: int = 0
    merged_findings: int = 0
    merged_consistent: int = 0
    fact_items: int = 0
    fact_unjudged: int = 0
    fact_incomplete: int = 0
    facts_expected: int = 0
    facts_final: int = 0
    facts_uncertain: int = 0
    facts_invalid: int = 0
    facts_correct: int = 0
    expected_by_verdict: Counter = field(default_factory=Counter)   # 預期的 verdict → 數量（Unjudged 的項目也算）
    found_by_verdict: Counter = field(default_factory=Counter)      # 預期的 verdict → 最終 verdict 正是它的數量
    failing_in_gold: int = 0   # Gold set 在這一組項目裡預期失敗的事實數，不論 Judge 跑了沒有：分辨「沒有這一格」和「這一格還沒跑」
    failing_expected: int = 0
    failing_found: int = 0
    present_final: int = 0     # 預期 Present，而且有最終 verdict
    false_alarms: int = 0      # 其中最終 verdict 是 Weakened 或 Absent


def expected_findings(item: GoldItem) -> list[JudgeFinding]:
    return [JudgeFinding(dimension=f.dimension, subtype="expected", severity=f.severity, source_span=f.source_span, target_span=f.target_span, reason="")
            for f in item.findings]


def is_current(call: GoldCall, digest: str, judge: PlannedJudge, template_hashes: dict[str, str]) -> bool:
    """這筆校準請求還適用嗎：項目送給 Judge 的內容、這個任務的 prompt、Judge 的設定都沒變。"""
    return (call.item_hash, call.prompt_hash, call.config_hash) == (digest, template_hashes[call.task], judge.config_hash)


def evaluate_judge(gold: GoldSet, truth: GroundTruth | None, calls: list[GoldCall], judge: PlannedJudge) -> JudgeEvaluation:
    template_hashes = {task: template.hash for task, template in task_templates().items()}
    digests = {i.id: item_hash(i, truth) for i in gold.items}
    fresh, stale = defaultdict(list), 0
    for call in calls:
        if call.judge != judge.id:
            continue
        if call.item_id in digests and is_current(call, digests[call.item_id], judge, template_hashes):
            fresh[(call.item_id, call.task)].append(call)
        else:
            stale += 1
    outcomes, requests = [], Counter()
    for item in gold.items:
        pair = item_pair(item)
        mqm_calls, fact_calls = fresh[(item.id, Task.MQM)], fresh[(item.id, Task.FACT)]
        requests.update({Task.MQM: len(mqm_calls), Task.FACT: len(fact_calls)})
        facts = judge_facts(fact_calls, item_sentences(item, truth)) if item.facts else None
        outcomes.append(ItemOutcome(item, pair, judge_component(mqm_calls, pair), facts,
                                    sum(len(c.findings) for c in mqm_calls), sum(1 for c in mqm_calls for i in c.invalid if i.kind is InvalidKind.FINDING)))
    return JudgeEvaluation(judge, outcomes, +requests, stale)


def _add_mqm(m: Metrics, outcome: ItemOutcome) -> None:
    item, judgment = outcome.item, outcome.mqm
    m.valid_findings += outcome.valid_findings
    m.invalid_findings += outcome.invalid_findings
    if judgment.status is Status.INCOMPLETE:
        m.mqm_incomplete += 1
        return
    expected = expected_findings(item)
    m.expected += len(expected)   # Unjudged 的項目也在分母：被擋的 Judge 什麼也抓不到
    if judgment.status is Status.UNJUDGED:
        m.mqm_unjudged += 1
        return
    m.mqm_judged += 1
    merged = judgment.findings
    matches = lambda e, f: same_finding(e, f.finding, outcome.pair)   # noqa: E731
    for e in expected:
        hits = [f for f in merged if matches(e, f)]
        m.found_any += bool(hits)
        m.found_consistent += any(f.consistent for f in hits)
        if hits:
            best = next((f for f in hits if f.consistent), hits[0])
            m.severity_pairs += 1
            m.severity_agree += best.finding.severity == e.severity
    m.merged_findings += len(merged)
    m.merged_consistent += sum(1 for f in merged if f.consistent)
    for f in merged:
        correct = any(matches(e, f) for e in expected)
        if item.category is GoldCategory.ANNOTATED:
            m.annotated_findings += 1
            m.annotated_correct += correct
        if item.category in COMPLETE_ANNOTATION:
            if f.consistent:
                m.consistent_findings += 1
                m.consistent_wrong += not correct
            else:
                m.uncertain_findings += 1
                m.uncertain_wrong += not correct
    if item.category is GoldCategory.CLEAN:
        m.clean_items += 1
        m.clean_flagged_any += bool(merged)
        m.clean_flagged_consistent += any(f.consistent for f in merged)


def _add_facts(m: Metrics, outcome: ItemOutcome) -> None:
    judgment = outcome.facts
    if judgment is None:
        return
    m.failing_in_gold += sum(1 for f in outcome.item.facts if f.verdict in FAILING_VERDICTS)
    if judgment.status is Status.INCOMPLETE:
        m.fact_incomplete += 1
        return
    unjudged = judgment.status is Status.UNJUDGED
    m.fact_unjudged += unjudged
    m.fact_items += not unjudged
    for expected in outcome.item.facts:
        m.facts_expected += 1
        m.expected_by_verdict[expected.verdict] += 1
        m.failing_expected += expected.verdict in FAILING_VERDICTS   # Unjudged 的事實也在分母：算沒有抓到
        if unjudged:
            continue
        result = judgment.facts[fact_key(expected.fact)]
        if result.status is FactStatus.UNCERTAIN:
            m.facts_uncertain += 1
        elif result.status is FactStatus.INVALID:
            m.facts_invalid += 1
        else:
            m.facts_final += 1
            m.facts_correct += result.verdict is expected.verdict
            m.found_by_verdict[expected.verdict] += result.verdict is expected.verdict
            if expected.verdict in FAILING_VERDICTS:
                m.failing_found += result.verdict in FAILING_VERDICTS
            else:
                m.present_final += 1
                m.false_alarms += result.verdict in FAILING_VERDICTS


def metrics_for(evaluation: JudgeEvaluation, *, direction=None, domain=None, category: GoldCategory | None = None) -> Metrics:
    m = Metrics()
    for outcome in evaluation.outcomes:
        item = outcome.item
        if (direction, domain, category) != (None, None, None) and not (
                direction in (None, item.direction) and domain in (None, item.domain) and category in (None, item.category)):
            continue
        m.items += 1
        _add_mqm(m, outcome)
        _add_facts(m, outcome)
    return m
