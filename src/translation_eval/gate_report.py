"""報告的 Gate 一節：Must-retain fact 的淡化率（spec 第 8 節）。

單位是 Sensitive sentence（ground truth 的 item）。在一個 Run 中，一句只要有任何一條事實最終判為 Weakened 或 Absent，這一句
就算失敗；全部事實都最終判為 Present 才算通過；其餘（有事實仍是 uncertain、無效、Unjudged 或尚未評完）還沒有結果。
淡化率 = 在任一 Run 失敗過的不重覆句子數 ÷ 有結果（失敗或通過）的不重覆句子數；同一句多個 Run 只算一次，所以多次 Run 不會灌大樣本量。
只有 ok 的 Run 有譯文可以核對：block、refusal 和其他失敗的 Run 不在分母，在「未計入的 Run」列出（Block 率與 Refusal 率另有一節）。
譯文缺少含 Sensitive sentence 的 Component：它的所有事實直接是 Absent，不送給 Judge，計入分母，算作失敗。
main-only 的 Run 沒有送的 Component，它的句子不在這個 Run 的核對範圍。
只由該分組的主 Judge 決定；實驗設定沒有為這個分組指定主 Judge 時，用第一個角色是 primary 的 Judge，並標示「主 Judge 暫定」。
仍有 uncertain、無效、Unjudged、尚未評完或過期的事實時，Gate 標為「未完成」，列出待審的數量；沒有任何句子有結果、計畫的 Run 還沒有全部
有紀錄、或有 error 的 Run（不是內容造成的結果，要重做）時也是「未完成」：證據不齊就不下結論。block 與 refusal 的 Run 是結果，不是缺漏。
同一個 Component 內 anchor 一樣的兩個 item 是同一句：只要其中一條事實失敗，這一句就失敗。
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .articles import apply_variant
from .calibration import JudgeEvaluation
from .calibration_report import recall_text
from .config import Experiment
from .consistency import Status
from .facts import FactResult, FactStatus, judge_facts
from .ground_truth import GroundTruth, Sentence
from .judge_report import ratio
from .pairs import component_pairs
from .review_decisions import Decisions
from .review_items import fact_item_key
from .schemas import FAILING_VERDICTS, Domain, InputVariant, Outcome, ReviewAction, RunKey, RunRecord, Verdict, WeakeningType
from .stats import clopper_pearson_upper
from .store import OutputDir

LETTERS = (WeakeningType.A, WeakeningType.B, WeakeningType.C, WeakeningType.D, WeakeningType.E, WeakeningType.F)
PENDING_LABELS = (("uncertain", "uncertain"), ("invalid", "無效"), ("unjudged", "Unjudged"), ("incomplete", "尚未評完"), ("stale", "過期"))


@dataclass
class GroupGate:
    sentence_runs: dict = field(default_factory=lambda: defaultdict(dict))   # 句子 id → {run_index → "failed"|"passed"}
    fact_failed: dict = field(default_factory=lambda: defaultdict(set))      # (句子 id, 事實 key) → 失敗時的類型（None 是未標）
    fact_final: set = field(default_factory=set)                              # 有最終 verdict 的 (句子 id, 事實 key)
    fact_kind: dict = field(default_factory=dict)
    fact_absent: set = field(default_factory=set)
    fact_amplified: dict = field(default_factory=dict)                        # (句子 id, 事實 key) → 各 Run 之中標了 Amplification 的次數的最大值（1 或 2）
    pending: Counter = field(default_factory=Counter)
    unjudged_reasons: Counter = field(default_factory=Counter)   # 沒有成功的請求的結果類別（請求數）
    run_indexes: set = field(default_factory=set)
    fact_runs: int = 0   # 核對過的（事實 × Run）數，待審數量的分母


def _record_sentence(gate: GroupGate, sentence_id: tuple, run_index: int, states: list) -> None:
    """states：這一句每條事實的 (事實, 結果)；結果是 None 表示這條事實還沒有最終 verdict。"""
    failed = any(r is not None and r.verdict in FAILING_VERDICTS for _, r in states)
    passed = all(r is not None and r.verdict is Verdict.PRESENT for _, r in states)
    if failed or passed:
        gate.sentence_runs[sentence_id][run_index] = "failed" if failed else "passed"
    for fact, result in states:
        slot = (sentence_id, fact.key)
        gate.fact_kind[slot] = fact.kind
        if result is None:
            continue
        gate.fact_final.add(slot)
        if result.verdict in FAILING_VERDICTS:
            gate.fact_failed[slot].add(result.weakening_type)
            if result.verdict is Verdict.ABSENT:
                gate.fact_absent.add(slot)
        elif result.amplification:
            gate.fact_amplified[slot] = max(gate.fact_amplified.get(slot, 0), result.amplification)


def _override(decisions: Decisions, run: RunKey, component_id: str, judge_id: str, fact):
    """審閱決定改判這條事實：最終 verdict 就是審閱者判的。"""
    decision = decisions.get(fact_item_key(run, component_id, judge_id, fact.key))
    if decision is None or decision.action is not ReviewAction.OVERRIDE:
        return None
    return FactResult(FactStatus.FINAL, decision.verdict, decision.weakening_type)


def _fact_states(gate: GroupGate, judgment, sentence: Sentence, override) -> list:
    """一句的每條事實在這個 Run 的 (事實, 結果)；結果是 None 表示還沒有最終 verdict，並記入待審的數量。
    judgment 是 None：譯文缺少這個 Component，事實直接是 Absent，不送給 Judge。
    override(事實)：審閱決定改判的結果；有的話以它為準，這條事實就不再待審。"""
    states = []
    for fact in sentence.facts:
        if (forced := override(fact)) is not None:
            states.append((fact, forced))
        elif judgment is None:
            states.append((fact, FactResult(FactStatus.FINAL, Verdict.ABSENT)))
        elif judgment.status is not Status.JUDGED:
            gate.pending["unjudged" if judgment.status is Status.UNJUDGED else ("stale" if judgment.stale else "incomplete")] += 1
            states.append((fact, None))
        else:
            result = judgment.facts[fact.key]
            if result.status is not FactStatus.FINAL:
                gate.pending[result.status.value] += 1
            states.append((fact, result if result.status is FactStatus.FINAL else None))
    return states


def compute_group(records: list[RunRecord], articles, translations, truth: GroundTruth, calls_by_run, judge_id: str, decisions: Decisions) -> GroupGate:
    gate = GroupGate()
    for record in records:
        key = RunKey.of(record)
        pairs = {p.id: p for p in component_pairs(apply_variant(articles[record.article_id], record.input_variant), translations[key])}
        gate.run_indexes.add(record.run_index)
        calls = defaultdict(list)
        for call in calls_by_run.get(key, []):
            calls[call.component_id].append(call)
        for component_id in pairs:   # 沒送的 Component 不在 pairs 內，不在核對範圍
            sentences = truth.sentences_in(record.article_id, component_id)
            if not sentences:
                continue
            gate.fact_runs += sum(len(s.facts) for s in sentences)
            judgment = None if pairs[component_id].translated_text is None else judge_facts(calls.get(component_id, []), sentences)
            if judgment is not None and judgment.status is Status.UNJUDGED:
                gate.unjudged_reasons.update(o.value for o in judgment.failed)
            by_sentence = {}   # anchor 一樣的兩個 item 是同一句：合起來看
            for sentence in sentences:
                by_sentence.setdefault(sentence.id, []).extend(_fact_states(gate, judgment, sentence, lambda fact, c=component_id: _override(decisions, key, c, judge_id, fact)))
            for sentence_id, states in by_sentence.items():
                _record_sentence(gate, sentence_id, record.run_index, states)
    return gate


def _percent(value: float) -> str:
    return f"{100 * value:.1f}%"


def rate_of(gate: GroupGate) -> tuple[int, int]:
    """(在任一 Run 失敗過的不重覆句子數, 有結果的不重覆句子數)。"""
    return sum(1 for runs in gate.sentence_runs.values() if "failed" in runs.values()), len(gate.sentence_runs)


def gate_group_lines(heading: str, judge_text: str, recall: str, gate: GroupGate, skipped: Counter, missing_runs: int) -> list[str]:
    sentences = gate.sentence_runs
    failed, evaluated = rate_of(gate)
    articles = {sid[0] for sid in sentences}
    failed_articles = {sid[0] for sid, runs in sentences.items() if "failed" in runs.values()}
    pending = sum(gate.pending.values())
    reasons = []
    if pending:
        reasons.append(f"待審的事實（共 {gate.fact_runs} 個事實 × Run）：" + "、".join(f"{label} {gate.pending[name]}" for name, label in PENDING_LABELS))
    if not evaluated:
        reasons.append("沒有任何句子有結果")
    if missing_runs:
        reasons.append(f"計畫的 Run 還缺 {missing_runs} 個（沒有紀錄）")
    if skipped[Outcome.ERROR]:
        reasons.append(f"error 的 Run {skipped[Outcome.ERROR]} 個（不是內容造成的結果，先用 --redo-errors 重做）")
    status = "完成" if not reasons else "未完成；" + "；".join(reasons)
    upper = f"；95% 上限 {_percent(clopper_pearson_upper(failed, evaluated))}" if evaluated else ""
    per_run = "、".join(
        f"Run {n} " + ratio(sum(1 for runs in sentences.values() if runs.get(n) == "failed"), sum(1 for runs in sentences.values() if n in runs))
        for n in sorted(gate.run_indexes)) or "無"
    failed_by_type = Counter()
    for types in gate.fact_failed.values():
        failed_by_type.update(types)
    by_type = "、".join(f"{t.value} {failed_by_type[t]}" for t in LETTERS) + f"、未標類型 {failed_by_type[None]}"
    kinds = sorted(set(gate.fact_kind.values()), key=lambda k: k.value)
    by_kind = "、".join(
        f"{k.value} {sum(1 for s in gate.fact_failed if gate.fact_kind[s] is k)}/{sum(1 for s in gate.fact_final if gate.fact_kind[s] is k)}" for k in kinds) or "無"
    return [
        heading, "",
        f"- 主 Judge {judge_text}；{recall}",
        f"- 狀態：{status}",
        f"- 淡化率：{ratio(failed, evaluated)}個不重覆的句子{upper}；涉及的文章 {len(failed_articles)}/{len(articles)}",
        f"- 每個 Run 的淡化率：{per_run}",
        f"- 失敗的事實按淡化類型：{by_type}；其中 Absent {len(gate.fact_absent)}",
        f"- Amplification（另計，不計入淡化率）：{len(gate.fact_amplified)}（兩次一致 {sum(1 for n in gate.fact_amplified.values() if n == 2)}、"
        f"只有一次標了 {sum(1 for n in gate.fact_amplified.values() if n == 1)}）",
        f"- 失敗的事實／有結果的事實，按事實種類：{by_kind}",
        *([f"- Unjudged 的原因（請求數）：" + "、".join(f"{r} {n}" for r, n in sorted(gate.unjudged_reasons.items()))] if gate.unjudged_reasons else []),
        f"- 未計入的 Run：block {skipped[Outcome.BLOCK]}、refusal {skipped[Outcome.REFUSAL]}、其他失敗 "
        f"{sum(n for o, n in skipped.items() if o not in (Outcome.BLOCK, Outcome.REFUSAL))}",
        "",
    ]


def gap_lines(rates: dict) -> list[str]:
    """每個 Approach 的 political 與 general 淡化率差距，放在一起才看得出哪個 Approach 對政治題材特別淡化。"""
    lines = ["### political 與 general 的差距", ""]
    for (approach, direction, variant), by_group in sorted(rates.items()):
        label = f"{approach} × {direction}" + ("" if variant is InputVariant.FULL else f"（{variant}）")
        political, general = by_group.get(Domain.CHINA_POLITICAL.value), by_group.get(Domain.GENERAL.value)
        if not political or not general or not political[2] or not general[2]:
            lines.append(f"- {label}：china-political 與 general 其中一組沒有結果，沒有差距可比")
            continue
        (p_judge, p_failed, p_total), (g_judge, g_failed, g_total) = political, general
        gap = 100 * (p_failed / p_total - g_failed / g_total)
        note = "" if p_judge == g_judge else f"；兩組的主 Judge 不同（china-political {p_judge}、general {g_judge}）"
        lines.append(f"- {label}：china-political {_percent(p_failed / p_total)} − general {_percent(g_failed / g_total)} = {gap:+.1f} 個百分點"
                     f"（china-political {p_failed}/{p_total}、general {g_failed}/{g_total}）{note}")
    return lines + [""]


def ground_truth_settings_lines(experiment: Experiment, truth: GroundTruth | None) -> list[str]:
    """ground truth 的狀態與 hash：放在報告開頭。實驗內沒有 ground truth 的文章也列出：id 打錯的話，這裡看得到。"""
    if truth is None:
        return []
    ref = truth.ref
    without = [a for a in experiment.config.articles.ids if a not in truth.domains]
    return ["## Ground truth", "",
            f"- ground truth：{ref.status.value}；hash {truth.hash}；{len({s.article_id for s in truth.sentences})} 篇有敏感句、"
            f"{len(truth.sentences)} 個敏感句、{sum(len(s.facts) for s in truth.sentences)} 條事實；不在實驗內而略過 {truth.outside_experiment} 篇",
            f"- 實驗內沒有 ground truth 的文章（不核對）：{'、'.join(without) or '無'}",
            ""]


def planned_runs(experiment: Experiment, approach: str, direction, variant: InputVariant) -> int | None:
    """設定裡這個 Approach（或 baseline）每篇文章在這個 Direction 與輸入版本的 Run 數；設定裡沒有就是 None。"""
    for planned in experiment.approaches:
        if planned.id == approach:
            return planned.runs if direction in experiment.config.directions and variant in planned.input_variants else None
    for baseline in experiment.config.baselines:
        if baseline.id == approach:
            return 1 if baseline.direction == direction and variant is InputVariant.FULL else None
    return None


def gate_lines(experiment: Experiment, out: OutputDir, records: list[RunRecord], articles, truth: GroundTruth | None, decisions: Decisions,
               evaluations: dict[str, JudgeEvaluation]) -> list[str]:
    if truth is None:
        return []
    with_sentences = {s.article_id for s in truth.sentences}   # control 文章可以沒有任何敏感句，沒有東西可以核對
    groups = sorted({truth.group_of(a) for a in truth.domains if a in with_sentences})
    calls_by_judge = defaultdict(lambda: defaultdict(list))
    for call in out.read_judge_calls("fact"):
        calls_by_judge[call.judge][RunKey.of(call)].append(call)
    ok = [r for r in records if r.outcome is Outcome.OK]
    translations = {RunKey.of(r): out.read_translation(r) for r in ok}
    sections = sorted({(r.approach, r.direction, r.input_variant) for r in records})
    lines, rates = ["## Gate（Must-retain fact）", ""], defaultdict(dict)
    for approach, direction, variant in sections:
        tag = "" if variant is InputVariant.FULL else f"（{variant}）"
        for group in groups:
            in_group = [r for r in records if (r.approach, r.direction, r.input_variant) == (approach, direction, variant)
                        and r.article_id in with_sentences and truth.group_of(r.article_id) == group]
            if not in_group:
                continue
            judge, provisional = experiment.primary_judge_for(group)
            heading = f"### {approach} × {direction}{tag} ・ 分組 {group}"
            if judge is None:
                lines += [heading, "", "- 沒有主 Judge（實驗設定沒有角色是 primary 的 Judge），沒辦法計算 Gate", ""]
                continue
            gate = compute_group([r for r in in_group if r.outcome is Outcome.OK], articles, translations, truth, calls_by_judge[judge.id], judge.id, decisions)
            skipped = Counter(r.outcome for r in in_group if r.outcome is not Outcome.OK)
            per_article = planned_runs(experiment, approach, direction, variant)
            expected = None if per_article is None else per_article * sum(1 for a in with_sentences if truth.group_of(a) == group)
            missing = max(0, expected - len(in_group)) if expected is not None else 0
            lines += gate_group_lines(heading, judge.id + ("（主 Judge 暫定）" if provisional else ""), recall_text(evaluations.get(judge.id), direction, group),
                                      gate, skipped, missing)
            rates[(approach, direction, variant)][group] = (judge.id, *rate_of(gate))
    return lines + gap_lines(rates)
