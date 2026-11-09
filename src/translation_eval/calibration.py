"""Judge 校準的計算：Gold set 的項目配上某個 Judge 的請求紀錄，算出各項指標、分歧清單，和 Gate 旁邊要列的 recall。純計算，沒有檔案。

只用「新的」請求紀錄：prompt、Judge 設定、項目內容（含它指向的事實）任何一項改了，舊的紀錄就過期、忽略，並計數。所以 prompt 一改，
recall 就回到「未量度」，直到重新校準：舊 prompt 量到的 recall 說不了新 prompt。
配對規則與 Judge 兩次結果的一致性相同（consistency.same_finding）：同一 Dimension、譯文片段重疊（譯文片段是空的就看原文片段）。

指標的定義（分母都寫在報告裡）：
  - 預期 Finding 的 recall：預期的 Finding 被 Judge 的 Finding 對上的比例，只計一致的與連 uncertain 一起計各一個；對上的之中 Severity 一樣的比例另計。
    分母是評完（兩個請求都有結果、至少一個成功）的項目的預期 Finding。
  - 驗證後 precision：只看 annotated 項目（標註是完整的）。Judge 的有效 Finding（逐字驗證通過、兩次合併之後）之中，對得上預期 Finding 的比例。
    無效的 Finding 不在內，在「無效 Finding 率」。variant 的 Judge Finding 沒有人逐條標過，不計入 precision。
  - clean 項目上的誤報：clean 項目之中，Judge 報了任何 Finding 的項目數。
  - Finding 的錯誤率：annotated 與 clean 項目（標註完整的）上，一致的 Finding 與 uncertain 的 Finding 各有多少對不上預期。用來檢驗「跑兩次」：
    若 uncertain 的錯誤率沒有比較高，兩次的不一致就沒有提供資訊。
  - 事實：只看項目列出預期 verdict 的事實。有最終 verdict（兩次一致）的才算準確與否；uncertain、無效的另計。Weakened／Absent 的 recall 是預期為
    該 verdict 的事實之中，Judge 的最終 verdict 正是它的比例；「失敗的事實」的 recall（Gate 旁邊列的）是預期 Weakened 或 Absent 的事實之中，最終
    verdict 是 Weakened 或 Absent 的比例，uncertain 算沒有抓到。預期 Present 的事實被判成失敗的，是 Gate 的誤報。
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .config import PlannedJudge
from .consistency import Judgment, Status, judge_component, same_finding
from .facts import FAILING_VERDICTS, FactJudgment, FactStatus, judge_facts
from .gold import GoldSet, item_hash, item_sentences
from .ground_truth import GroundTruth, fact_key
from .judge_report import ratio
from .pairs import ComponentPair
from .prompts import fact_template, judge_template
from .schemas import Domain, GoldCall, GoldCategory, GoldItem, JudgeFinding, Verdict


@dataclass
class ItemOutcome:
    item: GoldItem
    pair: ComponentPair
    mqm: Judgment
    facts: FactJudgment | None   # 項目沒有 facts 時是 None
    valid_findings: int          # 兩次請求裡通過驗證的 Finding 數（合併之前）
    invalid_findings: int


@dataclass
class JudgeEvaluation:
    judge: PlannedJudge
    outcomes: list[ItemOutcome]
    requests: Counter   # task → 新的請求數
    stale: int          # 過期而忽略的請求數


@dataclass
class Metrics:
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
    expected_by_verdict: Counter = field(default_factory=Counter)   # 預期的 verdict → 數量
    found_by_verdict: Counter = field(default_factory=Counter)      # 預期的 verdict → 最終 verdict 正是它的數量
    failing_expected: int = 0
    failing_found: int = 0
    false_alarms: int = 0   # 預期 Present，最終判成 Weakened 或 Absent


def item_pair(item: GoldItem) -> ComponentPair:
    return ComponentPair(item.id, item.component_type, item.source, item.translation)


def _expected_findings(item: GoldItem) -> list[JudgeFinding]:
    return [JudgeFinding(dimension=f.dimension, subtype="expected", severity=f.severity, source_span=f.source_span, target_span=f.target_span, reason="")
            for f in item.findings]


def evaluate_judge(gold: GoldSet, truth: GroundTruth | None, calls: list[GoldCall], judge: PlannedJudge) -> JudgeEvaluation:
    templates = {"mqm": judge_template().hash, "fact": fact_template().hash}
    hashes = {i.id: item_hash(i, truth) for i in gold.items}
    fresh, stale = defaultdict(list), 0
    for call in calls:
        if call.judge != judge.id:
            continue
        current = hashes.get(call.item_id) == call.item_hash and (call.prompt_hash, call.config_hash) == (templates[call.task], judge.config_hash)
        if current:
            fresh[(call.item_id, call.task)].append(call)
        else:
            stale += 1
    outcomes, requests = [], Counter()
    for item in gold.items:
        pair = item_pair(item)
        mqm_calls, fact_calls = fresh[(item.id, "mqm")], fresh[(item.id, "fact")]
        requests.update({"mqm": len(mqm_calls), "fact": len(fact_calls)})
        facts = judge_facts(fact_calls, item_sentences(item, truth)) if item.facts else None
        outcomes.append(ItemOutcome(item, pair, judge_component(mqm_calls, pair), facts,
                                    sum(len(c.findings) for c in mqm_calls), sum(1 for c in mqm_calls for i in c.invalid if i.kind == "finding")))
    return JudgeEvaluation(judge, outcomes, +requests, stale)


def _add_mqm(m: Metrics, outcome: ItemOutcome) -> None:
    item, judgment = outcome.item, outcome.mqm
    m.valid_findings += outcome.valid_findings
    m.invalid_findings += outcome.invalid_findings
    if judgment.status is Status.INCOMPLETE:
        m.mqm_incomplete += 1
        return
    if judgment.status is Status.UNJUDGED:
        m.mqm_unjudged += 1
        return
    m.mqm_judged += 1
    expected, merged = _expected_findings(item), judgment.findings
    matches = lambda e, f: same_finding(e, f.finding, outcome.pair)   # noqa: E731
    for e in expected:
        hits = [f for f in merged if matches(e, f)]
        m.expected += 1
        m.found_any += bool(hits)
        m.found_consistent += any(f.consistent for f in hits)
        if hits:
            best = next((f for f in hits if f.consistent), hits[0])
            m.severity_pairs += 1
            m.severity_agree += best.finding.severity == e.severity
    m.merged_findings += len(merged)
    m.merged_consistent += sum(1 for f in merged if f.consistent)
    complete_annotation = item.category in (GoldCategory.ANNOTATED, GoldCategory.CLEAN)
    for f in merged:
        correct = any(matches(e, f) for e in expected)
        if item.category is GoldCategory.ANNOTATED:
            m.annotated_findings += 1
            m.annotated_correct += correct
        if complete_annotation:
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
    if judgment.status is Status.INCOMPLETE:
        m.fact_incomplete += 1
        return
    if judgment.status is Status.UNJUDGED:
        m.fact_unjudged += 1
        return
    m.fact_items += 1
    for expected in outcome.item.facts:
        result = judgment.facts[fact_key(expected.fact)]
        m.facts_expected += 1
        m.expected_by_verdict[expected.verdict] += 1
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
            elif result.verdict in FAILING_VERDICTS:
                m.false_alarms += 1
        m.failing_expected += expected.verdict in FAILING_VERDICTS


def metrics_for(evaluation: JudgeEvaluation, direction=None, domain: Domain | None = None) -> Metrics:
    m = Metrics()
    for outcome in evaluation.outcomes:
        if (direction is not None and outcome.item.direction != direction) or (domain is not None and outcome.item.domain != domain):
            continue
        m.items += 1
        _add_mqm(m, outcome)
        _add_facts(m, outcome)
    return m


def metric_lines(m: Metrics) -> list[str]:
    """一組項目的指標，只有數字。"""
    fact_agreement = ratio(m.facts_final, m.facts_final + m.facts_uncertain)
    return [
        f"- 項目：{m.items} 條；MQM 評完 {m.mqm_judged}、Unjudged {m.mqm_unjudged}、尚未評完 {m.mqm_incomplete}；"
        f"fact 評完 {m.fact_items}、Unjudged {m.fact_unjudged}、尚未評完 {m.fact_incomplete}",
        f"- 預期 Finding 的 recall：只計一致的 {ratio(m.found_consistent, m.expected)}；連 uncertain 一起計 {ratio(m.found_any, m.expected)}；"
        f"對上的之中 Severity 一致 {ratio(m.severity_agree, m.severity_pairs)}",
        f"- 驗證後 precision（annotated 項目）：{ratio(m.annotated_correct, m.annotated_findings)}",
        f"- clean 項目上的誤報：有 Finding 的 clean 項目，只計一致的 {ratio(m.clean_flagged_consistent, m.clean_items)}；連 uncertain 一起計 {ratio(m.clean_flagged_any, m.clean_items)}",
        f"- 無效 Finding 率：{ratio(m.invalid_findings, m.valid_findings + m.invalid_findings)}",
        f"- 兩次一致率：Finding {ratio(m.merged_consistent, m.merged_findings)}；事實 verdict {fact_agreement}",
        f"- Finding 的錯誤率（annotated 與 clean 項目）：一致的 {ratio(m.consistent_wrong, m.consistent_findings)}；uncertain 的 {ratio(m.uncertain_wrong, m.uncertain_findings)}",
        f"- 事實 verdict：有最終 verdict {ratio(m.facts_final, m.facts_expected)}（uncertain {m.facts_uncertain}、無效 {m.facts_invalid}）；"
        f"準確率（有最終 verdict 的）{ratio(m.facts_correct, m.facts_final)}",
        f"- Weakened 的 recall：{ratio(m.found_by_verdict[Verdict.WEAKENED], m.expected_by_verdict[Verdict.WEAKENED])}；"
        f"Absent 的 recall：{ratio(m.found_by_verdict[Verdict.ABSENT], m.expected_by_verdict[Verdict.ABSENT])}；"
        f"失敗的事實（Weakened 或 Absent）的 recall：{ratio(m.failing_found, m.failing_expected)}；預期 Present 被判成失敗：{m.false_alarms}",
    ]


def summary_lines(gold: GoldSet, evaluations: list[JudgeEvaluation]) -> list[str]:
    """報告的「Judge 校準摘要」：每個有校準紀錄的 Judge 的整體指標（只有數字，摘要版也可以放）。完整的分 Direction、domain 在 calibration.md。"""
    recorded = [e for e in evaluations if e.requests or e.stale]
    lines = ["## Judge 校準摘要", "", f"- Gold set：{len(gold.items)} 條（hash {gold.hash}）；合格線尚未訂", ""]
    for evaluation in recorded:
        judge = evaluation.judge
        lines += [f"### Judge {judge.id}（{judge.config.role}）", "",
                  f"- 新的請求：MQM {evaluation.requests['mqm']}、fact {evaluation.requests['fact']}；過期而忽略 {evaluation.stale}", *metric_lines(metrics_for(evaluation)), ""]
    if not recorded:
        lines += ["還沒有校準紀錄。", ""]
    return lines


def evaluation_lines(gold: GoldSet, evaluation: JudgeEvaluation, heading_level: str = "##") -> list[str]:
    judge = evaluation.judge
    lines = [f"{heading_level} Judge {judge.id}（{judge.config.role}）", "",
             f"- 新的請求：MQM {evaluation.requests['mqm']}、fact {evaluation.requests['fact']}；過期而忽略 {evaluation.stale}"
             f"（prompt、Judge 設定或項目內容改了）；設定 hash {judge.config_hash}", ""]
    groups = [("全部", metrics_for(evaluation))]
    groups += [(f"Direction {d}", metrics_for(evaluation, direction=d)) for d in sorted({i.direction for i in gold.items})]
    groups += [(f"domain {d}", metrics_for(evaluation, domain=d)) for d in sorted({i.domain for i in gold.items})]
    for title, metrics in groups:
        lines += [f"{heading_level}# {title}", "", *metric_lines(metrics), ""]
    return lines


def recall_text(evaluation: JudgeEvaluation | None, direction, group: str) -> str:
    """Gate 旁邊列的 Judge recall：主 Judge 在 Gold set 上，失敗的事實（Weakened 或 Absent）的 recall，按 Direction 與 domain 取。
    沒有校準、校準過期、或 Gold set 在這一格沒有預期失敗的事實，都明確寫「未量度」。group 不是 domain（例如 Q34 的覆寫分組）時不分 domain。"""
    if evaluation is None or not (evaluation.requests or evaluation.stale):
        return "Judge recall 未量度（還沒有校準結果）"
    if not evaluation.requests:
        return "Judge recall 未量度（校準的請求都已過期：prompt、Judge 設定或 Gold set 項目改了，要重新校準）"
    domain = Domain(group) if group in {d.value for d in Domain} else None
    m = metrics_for(evaluation, direction=direction, domain=domain)
    where = f"{direction}" + (f" × {domain.value}" if domain else "")
    if not m.failing_expected:
        return f"Judge recall 未量度（Gold set 在這一格沒有預期失敗的事實，或這些項目還沒評完：{where}）"
    pending = m.fact_incomplete + m.fact_unjudged
    note = f"；{pending} 條項目還沒評完或 Unjudged" if pending else ""
    stale = f"；有 {evaluation.stale} 個過期的請求被忽略" if evaluation.stale else ""
    return (f"Judge recall {ratio(m.failing_found, m.failing_expected)}（Gold set 上預期失敗的事實，Judge {evaluation.judge.id}，{where}；"
            f"uncertain 算沒有抓到{note}{stale}；合格線尚未訂）")


# ───────────── 分歧清單 ─────────────

def _finding_text(f: JudgeFinding) -> str:
    return f"{f.dimension.value}／{f.severity.value}：原文「{' '.join(f.source_span.split())}」譯文「{' '.join(f.target_span.split())}」"


def _quote(text: str) -> str:
    return "\n  > " + text.replace("\n", "\n  > ")


def _unmatched(pair: ComponentPair, findings: list[JudgeFinding], others: list[JudgeFinding]) -> list[JudgeFinding]:
    return [f for f in findings if not any(same_finding(f, o, pair) for o in others)]


def _fact_view(outcome: ItemOutcome, expected_fact) -> str:
    judgment = outcome.facts
    if judgment is None or judgment.status is not Status.JUDGED:
        return "沒有結果" if judgment is None or judgment.status is Status.INCOMPLETE else "Unjudged"
    result = judgment.facts[fact_key(expected_fact.fact)]
    return result.verdict.value if result.status is FactStatus.FINAL else result.status.value


def disagreement_lines(gold: GoldSet, evaluations: list[JudgeEvaluation]) -> list[str]:
    """Judge 對標註、Judge 對 Judge 不同的地方。含原文與譯文，只寫在 data/。"""
    lines = ["# 校準的分歧清單", "",
             "Judge 對標註：預期的 Finding 沒被抓到、Judge 多報（annotated 與 clean 項目上對不上預期的 Finding）、事實的 verdict 與預期不同。"
             "Judge 對 Judge：兩個 Judge 的 Finding 或事實 verdict 不一樣。含原文與譯文，只在本機看。", ""]
    judged = [e for e in evaluations if e.outcomes]
    if not judged:
        return lines + ["還沒有校準紀錄。", ""]
    by_judge = {e.judge.id: {o.item.id: o for o in e.outcomes} for e in judged}
    found = 0
    for item in gold.items:
        entries = []
        for evaluation in judged:
            outcome = by_judge[evaluation.judge.id][item.id]
            who = evaluation.judge.id
            if outcome.mqm.status is Status.JUDGED:
                merged = [m.finding for m in outcome.mqm.findings]
                expected = _expected_findings(item)
                entries += [f"Judge {who} 沒有抓到預期的 Finding：{_finding_text(e)}" for e in _unmatched(outcome.pair, expected, merged)]
                if item.category in (GoldCategory.ANNOTATED, GoldCategory.CLEAN):
                    entries += [f"Judge {who} 多報（標註沒有）：{_finding_text(f)}" for f in _unmatched(outcome.pair, merged, expected)]
            for fact in item.facts:
                view = _fact_view(outcome, fact)
                if view != fact.verdict.value and view != "沒有結果":
                    entries.append(f"Judge {who} 對事實「{' '.join(fact.fact.split())}」：{view}（預期 {fact.verdict.value}）")
        for i, first in enumerate(judged):
            for second in judged[i + 1:]:
                a, b = by_judge[first.judge.id][item.id], by_judge[second.judge.id][item.id]
                if a.mqm.status is Status.JUDGED and b.mqm.status is Status.JUDGED:
                    fa, fb = [m.finding for m in a.mqm.findings], [m.finding for m in b.mqm.findings]
                    entries += [f"只有 Judge {first.judge.id} 報：{_finding_text(f)}" for f in _unmatched(item_pair(item), fa, fb)]
                    entries += [f"只有 Judge {second.judge.id} 報：{_finding_text(f)}" for f in _unmatched(item_pair(item), fb, fa)]
                for fact in item.facts:
                    va, vb = _fact_view(a, fact), _fact_view(b, fact)
                    if va != vb and "沒有結果" not in (va, vb):
                        entries.append(f"事實「{' '.join(fact.fact.split())}」：Judge {first.judge.id} {va}、Judge {second.judge.id} {vb}")
        if entries:
            found += 1
            lines += [f"## `{item.id}` ・ {item.category.value} ・ {item.direction.value} ・ {item.domain.value}", "", f"- 原文：{_quote(item.source)}",
                      f"- 譯文：{_quote(item.translation)}", *[f"- {e}" for e in dict.fromkeys(entries)], ""]
    if not found:
        lines += ["沒有分歧。", ""]
    return lines

