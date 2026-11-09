"""校準的報告：把 calibration.py 算出的指標寫成 Markdown 的行，加上 Gate 旁邊的 recall 和分歧清單。

calibration.md 與報告裡的「Judge 校準摘要」只有數字；分歧清單含原文與譯文，只寫在 data/。
"""
from .calibration import (COMPLETE_ANNOTATION, ItemOutcome, JudgeEvaluation, Metrics, expected_findings, metrics_for)
from .consistency import Status, same_finding
from .facts import FactStatus
from .gold import GoldSet, item_pair
from .ground_truth import fact_key
from .judge_report import ratio
from .schemas import Domain, GoldCategory, JudgeFinding, Verdict
from .textrules import quoted


def metric_lines(m: Metrics) -> list[str]:
    """一組項目的指標，只有數字；每個比率都附分子和分母。"""
    fact_agreement = ratio(m.facts_final, m.facts_final + m.facts_uncertain)
    return [
        f"- 項目：{m.items} 條；MQM 評完 {m.mqm_judged}、Unjudged {m.mqm_unjudged}、尚未評完 {m.mqm_incomplete}；"
        f"fact 評完 {m.fact_items}、Unjudged {m.fact_unjudged}、尚未評完 {m.fact_incomplete}",
        f"- Unjudged 率（跑完的項目之中）：MQM {ratio(m.mqm_unjudged, m.mqm_judged + m.mqm_unjudged)}；fact {ratio(m.fact_unjudged, m.fact_items + m.fact_unjudged)}",
        f"- 預期 Finding 的 recall：只計一致的 {ratio(m.found_consistent, m.expected)}；連 uncertain 一起計 {ratio(m.found_any, m.expected)}"
        f"（Unjudged 的項目算沒有抓到）；對上的之中 Severity 一致 {ratio(m.severity_agree, m.severity_pairs)}",
        f"- 驗證後 precision（annotated 項目）：{ratio(m.annotated_correct, m.annotated_findings)}",
        f"- clean 項目上的誤報：有 Finding 的 clean 項目，只計一致的 {ratio(m.clean_flagged_consistent, m.clean_items)}；連 uncertain 一起計 {ratio(m.clean_flagged_any, m.clean_items)}",
        f"- 無效 Finding 率：{ratio(m.invalid_findings, m.valid_findings + m.invalid_findings)}",
        f"- 兩次一致率：Finding {ratio(m.merged_consistent, m.merged_findings)}；事實 verdict {fact_agreement}",
        f"- Finding 的錯誤率（annotated 與 clean 項目）：一致的 {ratio(m.consistent_wrong, m.consistent_findings)}；uncertain 的 {ratio(m.uncertain_wrong, m.uncertain_findings)}",
        f"- 事實 verdict：有最終 verdict {ratio(m.facts_final, m.facts_expected)}（uncertain {m.facts_uncertain}、無效 {m.facts_invalid}）；"
        f"準確率（有最終 verdict 的）{ratio(m.facts_correct, m.facts_final)}",
        f"- Weakened 的 recall：{ratio(m.found_by_verdict[Verdict.WEAKENED], m.expected_by_verdict[Verdict.WEAKENED])}；"
        f"Absent 的 recall：{ratio(m.found_by_verdict[Verdict.ABSENT], m.expected_by_verdict[Verdict.ABSENT])}；"
        f"失敗的事實（Weakened 或 Absent）的 recall：{ratio(m.failing_found, m.failing_expected)}（uncertain 與 Unjudged 算沒有抓到）；"
        f"預期 Present 被判成失敗：{ratio(m.false_alarms, m.present_final)}",
    ]


def _groups(gold: GoldSet, evaluation: JudgeEvaluation) -> list[tuple[str, Metrics]]:
    """全部、按 Direction、按 domain、Direction × domain（Gate 旁邊的 recall 取這一格），和 variant 按 domain（Q31 比較 1 的 recall）。"""
    directions = sorted({i.direction for i in gold.items})
    domains = sorted({i.domain for i in gold.items})
    groups = [("全部", metrics_for(evaluation))]
    groups += [(f"Direction {d}", metrics_for(evaluation, direction=d)) for d in directions]
    groups += [(f"domain {d}", metrics_for(evaluation, domain=d)) for d in domains]
    groups += [(f"Direction {d} × domain {o}", metrics_for(evaluation, direction=d, domain=o)) for d in directions for o in domains]
    groups += [(f"variant × domain {o}", metrics_for(evaluation, domain=o, category=GoldCategory.VARIANT)) for o in domains]
    return groups


def evaluation_lines(gold: GoldSet, evaluation: JudgeEvaluation) -> list[str]:
    judge = evaluation.judge
    lines = [f"## Judge {judge.id}（{judge.config.role}）", "",
             f"- 新的請求：MQM {evaluation.requests['mqm']}、fact {evaluation.requests['fact']}；過期而忽略 {evaluation.stale}"
             f"（prompt、Judge 設定或項目送給 Judge 的內容改了）；設定 hash {judge.config_hash}", ""]
    for title, metrics in _groups(gold, evaluation):
        if metrics.items:
            lines += [f"### {title}", "", *metric_lines(metrics), ""]
    return lines


def summary_lines(gold: GoldSet, evaluations: list[JudgeEvaluation]) -> list[str]:
    """報告的「Judge 校準摘要」：每個有校準紀錄的 Judge 的整體指標（只有數字，摘要版也可以放）。完整的分組在 calibration.md。"""
    recorded = [e for e in evaluations if e.has_records]
    lines = ["## Judge 校準摘要", "", f"- Gold set：{len(gold.items)} 條（hash {gold.hash}）；合格線尚未訂", ""]
    for evaluation in recorded:
        judge = evaluation.judge
        lines += [f"### Judge {judge.id}（{judge.config.role}）", "",
                  f"- 新的請求：MQM {evaluation.requests['mqm']}、fact {evaluation.requests['fact']}；過期而忽略 {evaluation.stale}", *metric_lines(metrics_for(evaluation)), ""]
    if not recorded:
        lines += ["還沒有校準紀錄。", ""]
    return lines


def recall_text(evaluation: JudgeEvaluation | None, direction, group: str) -> str:
    """Gate 旁邊列的 Judge recall：主 Judge 在 Gold set 上，失敗的事實（Weakened 或 Absent）的 recall，按 Direction 與 domain 取。
    沒有校準、校準過期、Gold set 在這一格沒有預期失敗的事實、或這一格的項目還沒有請求結果，都明確寫「未量度」並寫明原因。
    group 不是 domain（例如 Q34 的覆寫分組）時不分 domain。Judge 被擋（Unjudged）的項目算沒有抓到，不是不在分母。"""
    if evaluation is None or not evaluation.has_records:
        return "Judge recall 未量度（還沒有校準結果）"
    if not evaluation.requests:
        return "Judge recall 未量度（校準的請求都已過期：prompt、Judge 設定或 Gold set 項目改了，要重新校準）"
    domain = Domain(group) if group in {d.value for d in Domain} else None
    m = metrics_for(evaluation, direction=direction, domain=domain)
    where = f"{direction}" + (f" × {domain.value}" if domain else "")
    if not m.failing_in_gold:
        return f"Judge recall 未量度（Gold set 在這一格沒有預期失敗的事實：{where}）"
    if not m.failing_expected:
        return f"Judge recall 未量度（這一格的 {m.fact_incomplete} 條項目還沒有請求結果：尚未跑，或請求已過期：{where}）"
    pending = f"；另有 {m.fact_incomplete} 條項目還沒有請求結果" if m.fact_incomplete else ""
    unjudged = f"；其中 {m.fact_unjudged} 條項目 Judge 被擋（Unjudged，算沒有抓到）" if m.fact_unjudged else ""
    stale = f"；有 {evaluation.stale} 個過期的請求被忽略" if evaluation.stale else ""
    return (f"Judge recall {ratio(m.failing_found, m.failing_expected)}（Gold set 上預期失敗的事實，Judge {evaluation.judge.id}，{where}；"
            f"uncertain 算沒有抓到{unjudged}{pending}{stale}；合格線尚未訂）")


# ───────────── 分歧清單 ─────────────

def _finding_text(f: JudgeFinding) -> str:
    return f"{f.dimension.value}／{f.severity.value}：原文「{' '.join(f.source_span.split())}」譯文「{' '.join(f.target_span.split())}」"


def _unmatched(pair, findings: list[JudgeFinding], others: list[JudgeFinding]) -> list[JudgeFinding]:
    return [f for f in findings if not any(same_finding(f, o, pair) for o in others)]


def _severity_differences(pair, expected: list[JudgeFinding], merged: list[JudgeFinding]) -> list[str]:
    out = []
    for e in expected:
        hit = next((f for f in merged if same_finding(e, f, pair)), None)
        if hit is not None and hit.severity != e.severity:
            out.append(f"Severity 不同（預期 {e.severity.value}、Judge {hit.severity.value}）：{_finding_text(e)}")
    return out


def _fact_view(outcome: ItemOutcome, expected_fact) -> tuple[str, str | None]:
    """(Judge 對這條事實的結果, 它標的淡化類型)。沒有結果是「沒有結果」；Judge 被擋是「Unjudged」。"""
    judgment = outcome.facts
    if judgment is None or judgment.status is Status.INCOMPLETE:
        return "沒有結果", None
    if judgment.status is Status.UNJUDGED:
        return "Unjudged", None
    result = judgment.facts[fact_key(expected_fact.fact)]
    if result.status is not FactStatus.FINAL:
        return result.status.value, None
    return result.verdict.value, result.weakening_type.value if result.weakening_type else None


def _annotation_entries(who: str, item, outcome: ItemOutcome) -> list[str]:
    """一個 Judge 對一個項目：和標註不一樣的地方。"""
    entries = []
    if outcome.mqm.status is Status.JUDGED:
        merged, expected = [m.finding for m in outcome.mqm.findings], expected_findings(item)
        entries += [f"Judge {who} 沒有抓到預期的 Finding：{_finding_text(e)}" for e in _unmatched(outcome.pair, expected, merged)]
        entries += [f"Judge {who}：{line}" for line in _severity_differences(outcome.pair, expected, merged)]
        if item.category in COMPLETE_ANNOTATION:
            entries += [f"Judge {who} 多報（標註沒有）：{_finding_text(f)}" for f in _unmatched(outcome.pair, merged, expected)]
    for fact in item.facts:
        view, kind = _fact_view(outcome, fact)
        expected_kind = fact.weakening_type.value if fact.weakening_type else None
        text = ' '.join(fact.fact.split())
        if view not in (fact.verdict.value, "沒有結果"):
            entries.append(f"Judge {who} 對事實「{text}」：{view}（預期 {fact.verdict.value}）")
        elif view == fact.verdict.value and kind and expected_kind and kind != expected_kind:
            entries.append(f"Judge {who} 對事實「{text}」：{view} 的類型是 {kind}（預期 {expected_kind}）")
    return entries


def _pair_entries(item, first: ItemOutcome, second: ItemOutcome, names: tuple[str, str]) -> list[str]:
    """兩個 Judge 對同一個項目：彼此不一樣的地方。"""
    entries, pair = [], item_pair(item)
    if first.mqm.status is Status.JUDGED and second.mqm.status is Status.JUDGED:
        a, b = [m.finding for m in first.mqm.findings], [m.finding for m in second.mqm.findings]
        entries += [f"只有 Judge {names[0]} 報：{_finding_text(f)}" for f in _unmatched(pair, a, b)]
        entries += [f"只有 Judge {names[1]} 報：{_finding_text(f)}" for f in _unmatched(pair, b, a)]
    for fact in item.facts:
        (va, _), (vb, _) = _fact_view(first, fact), _fact_view(second, fact)
        if va != vb and "沒有結果" not in (va, vb):
            entries.append(f"事實「{' '.join(fact.fact.split())}」：Judge {names[0]} {va}、Judge {names[1]} {vb}")
    return entries


def disagreement_lines(gold: GoldSet, evaluations: list[JudgeEvaluation]) -> list[str]:
    """Judge 對標註、Judge 對 Judge 不同的地方。含原文與譯文，只寫在 data/。"""
    lines = ["# 校準的分歧清單", "",
             "Judge 對標註：預期的 Finding 沒被抓到或 Severity 不同、Judge 多報（annotated 與 clean 項目上對不上預期的 Finding）、事實的 verdict 或類型與預期不同。"
             "Judge 對 Judge：兩個 Judge 的 Finding 或事實 verdict 不一樣。含原文與譯文，只在本機看。", ""]
    judged = [e for e in evaluations if e.has_records]
    if not judged:
        return lines + ["還沒有校準紀錄。", ""]
    by_judge = {e.judge.id: {o.item.id: o for o in e.outcomes} for e in judged}
    found = 0
    for item in gold.items:
        entries = [line for e in judged for line in _annotation_entries(e.judge.id, item, by_judge[e.judge.id][item.id])]
        for i, first in enumerate(judged):
            for second in judged[i + 1:]:
                entries += _pair_entries(item, by_judge[first.judge.id][item.id], by_judge[second.judge.id][item.id], (first.judge.id, second.judge.id))
        if entries:
            found += 1
            lines += [f"## `{item.id}` ・ {item.category.value} ・ {item.direction.value} ・ {item.domain.value}", "", f"- 原文：{quoted(item.source)}",
                      f"- 譯文：{quoted(item.translation)}", *[f"- {e}" for e in dict.fromkeys(entries)], ""]
    return lines + ([] if found else ["沒有分歧。", ""])
