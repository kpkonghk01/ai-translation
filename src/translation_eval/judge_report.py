"""報告的 Judge 章節：評完多少、Unjudged 率、無效 Finding 率、分數，以及 Source defect 清單。

分數由程式按規則計算，Judge 只給 Finding（spec 第 7 節）：
  每個 Finding 的罰分 = 該 Severity 的權重；Fidelity-of-force 不計分（它由 Gate 處理，只在這裡列數量）。
  Deterministic 的 Finding（check 產生）一律計入，包括漏掉的 Component 的漏段 Finding 和多出的 Component：否則漏譯的譯文
  會比完整的譯文分數更好。Judge 的 Finding 只有評完的 Component 才有；Judge 沒評完或 Unjudged 的 Component，沒有 Judge 的
  Finding，這個缺口由「評完」和 Unjudged 率呈報。
  Main 與 Peripheral 分開；兩個數字：只計一致的 Finding，以及連 uncertain 一起計。每千字原文的罰分的分母是這些 Run 的
  原文總字數（不計空白），包括漏掉的 Component 的原文。
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .articles import apply_variant, is_main_type
from .config import Experiment
from .consistency import Status, judge_component
from .pairs import ComponentPair, component_pairs
from .schemas import ComponentResult, Dimension, InputVariant, JudgeCall, ReviewAction, RunKey, RunRecord, Severity, Weights
from .prompts import judge_template
from .review_decisions import Decisions
from .review_items import finding_item_key
from .store import OutputDir
from .textrules import nonspace_length, squash

SEVERITIES = (Severity.MINOR, Severity.MAJOR, Severity.CRITICAL)


@dataclass
class RoleScore:
    penalty_consistent: float = 0.0
    penalty_all: float = 0.0
    chars: int = 0
    consistent: Counter = field(default_factory=Counter)   # (Dimension, Severity) → 數量
    all: Counter = field(default_factory=Counter)


@dataclass
class GroupScore:
    components: int = 0
    incomplete: int = 0
    unjudged: int = 0
    unjudged_reasons: Counter = field(default_factory=Counter)
    valid_findings: int = 0
    invalid_findings: int = 0
    fof_consistent: int = 0
    fof_uncertain: int = 0
    severity_mismatches: int = 0
    main: RoleScore = field(default_factory=RoleScore)
    peripheral: RoleScore = field(default_factory=RoleScore)


def _one_line(text: str) -> str:
    """Judge 寫的文字放進報告前先壓成一行，才不會在報告裡開出新的行或標題。"""
    return " ".join(text.split())


def _number(value: float) -> str:
    return f"{value:g}"


def ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}（{100 * numerator / denominator:.1f}%）" if denominator else f"{numerator}/{denominator}（—）"


def _add(role: RoleScore, dimension: Dimension, severity: Severity, weights: Weights, consistent: bool) -> None:
    penalty = getattr(weights, severity.value)
    role.penalty_all += penalty
    role.all[(dimension, severity)] += 1
    if consistent:
        role.penalty_consistent += penalty
        role.consistent[(dimension, severity)] += 1


def score_group(records: list[RunRecord], articles, translations, calls_by_run, rows_by_run, weights: Weights, judge_id: str, decisions: Decisions) -> GroupScore:
    score = GroupScore()
    for record in records:
        key = RunKey.of(record)
        pairs = component_pairs(apply_variant(articles[record.article_id], record.input_variant), translations[key])
        calls = defaultdict(list)
        for call in calls_by_run.get(key, []):
            calls[call.component_id].append(call)
            score.valid_findings += len(call.findings)
            score.invalid_findings += sum(1 for item in call.invalid if item.kind == "finding")
        for row in rows_by_run.get(key, []):   # Deterministic 的 Finding 一律計入，包括漏段和多出的 Component
            role = score.main if is_main_type(row.source_type) else score.peripheral
            for finding in row.findings:
                _add(role, finding.dimension, finding.severity, weights, consistent=True)
        for pair in pairs:
            (score.main if pair.is_main else score.peripheral).chars += nonspace_length(pair.source_text)
            if pair.translated_text is None:
                continue   # 譯文沒有這個 Component：沒有東西可以給 Judge 評
            score.components += 1
            judgment = judge_component(calls.get(pair.id, []), pair)
            if judgment.status is Status.INCOMPLETE:
                score.incomplete += 1
                continue
            if judgment.status is Status.UNJUDGED:
                score.unjudged += 1
                score.unjudged_reasons.update(o.value for o in judgment.failed)
                continue
            role = score.main if pair.is_main else score.peripheral
            for merged in judgment.findings:
                decision = decisions.get(finding_item_key(key, pair.id, judge_id, merged.finding))
                if decision is not None and decision.action is ReviewAction.REJECT:   # 審閱駁回：不計分，也不計數
                    continue
                reviewed = decision is not None   # accept 或 override：審閱者確認了，算作一致
                severity = decision.severity if decision is not None and decision.severity else merged.finding.severity
                score.severity_mismatches += merged.severity_mismatch
                if merged.finding.dimension is Dimension.FIDELITY_OF_FORCE:
                    score.fof_consistent += merged.consistent or reviewed
                    score.fof_uncertain += not (merged.consistent or reviewed)
                else:
                    _add(role, merged.finding.dimension, severity, weights, merged.consistent or reviewed)
    return score


def _breakdown(counter: Counter, weights: Weights) -> str:
    order = [(d, s) for d in Dimension for s in SEVERITIES]
    parts = [f"{d.value}/{s.value} {counter[(d, s)]}（{_number(counter[(d, s)] * getattr(weights, s.value))}）" for d, s in order if counter[(d, s)]]
    return "、".join(parts) or "無"


def _role_lines(name: str, role: RoleScore, weights: Weights) -> list[str]:
    per_1000 = lambda penalty: f"{(penalty / role.chars * 1000) if role.chars else 0.0:.1f}"   # noqa: E731
    return [
        f"- {name}：總罰分 {_number(role.penalty_consistent)}（只計一致的 Finding）／{_number(role.penalty_all)}（連 uncertain 一起計）；"
        f"每千字原文的罰分 {per_1000(role.penalty_consistent)}／{per_1000(role.penalty_all)}（原文 {role.chars} 字）",
        f"- {name} 按 Dimension 與 Severity：只計一致：{_breakdown(role.consistent, weights)}；連 uncertain 一起計：{_breakdown(role.all, weights)}",
    ]


def group_lines(heading: str, score: GroupScore, weights: Weights) -> list[str]:
    complete = score.components - score.incomplete
    unjudged = f"- Unjudged 率：{ratio(score.unjudged, complete)}"
    if score.unjudged_reasons:
        unjudged += "；原因（請求數）：" + "、".join(f"{reason} {n}" for reason, n in sorted(score.unjudged_reasons.items()))
    return [
        heading, "",
        f"- 評完：{complete}/{score.components} 個 Component（尚未評完 {score.incomplete}）",
        unjudged,
        f"- 無效 Finding 率：{ratio(score.invalid_findings, score.valid_findings + score.invalid_findings)}",
        *_role_lines("Main", score.main, weights),
        *_role_lines("Peripheral", score.peripheral, weights),
        f"- Fidelity-of-force（不計分，交人手審閱）：一致 {score.fof_consistent}、uncertain {score.fof_uncertain}",
        f"- Severity 兩次不一致的 Finding：{score.severity_mismatches}",
        "",
    ]


def header_lines(experiment: Experiment, calls: list[JudgeCall]) -> list[str]:
    """Judge 的設定、同家族的例外、權重與正規化方式：放在報告開頭，看結果之前先看到。"""
    judging, template = experiment.config.judging, judge_template()
    lines = ["## Judge 設定", ""]
    for judge in experiment.judges:
        mine = [c for c in calls if c.judge == judge.id]
        stale = sum(1 for c in mine if (c.prompt_hash, c.config_hash) != (template.hash, judge.config_hash))
        c = judge.config
        lines.append(f"- {c.id}（{c.role}）：家族 {c.family}；provider {c.provider}；模型 {c.model}；prompt judge-mqm {template.hash}；"
                     f"設定 hash {judge.config_hash}（{len(mine)} 個請求）" + (f"；⚠ 有 {stale} 個請求是用不同的設定或 prompt 跑的" if stale else ""))
    lines += [f"- 同家族的例外：{e.approach} × {e.judge}：{e.reason}" for e in judging.same_family_exceptions]
    weights = experiment.config.weights
    lines.append(f"- 權重：minor {_number(weights.minor)}、major {_number(weights.major)}、critical {_number(weights.critical)}")
    lines.append("- 正規化：每千字原文的罰分，分母是評完的 Component 的原文字數（不計空白）")
    if judging.mqm_run_limit:
        lines.append("- MQM 只評：" + "；".join(f"{a} 的第 {n} 個 Run 以內" for a, n in sorted(judging.mqm_run_limit.items())))
    return lines + [""]


def source_defect_lines(calls: list[JudgeCall]) -> list[str]:
    """所有 Judge 在所有請求裡列出的 Source defect，以「文章、Component、原文片段」去重，不按 Approach 重覆。"""
    found: dict[tuple, tuple[str, set]] = {}
    spans: dict[tuple, str] = {}
    for call in calls:
        for defect in call.source_defects:
            key = (call.article_id, call.component_id, squash(defect.source_span))
            issue, judges = found.setdefault(key, (_one_line(defect.issue), set()))
            spans.setdefault(key, _one_line(defect.source_span))
            judges.add(call.judge)
    lines = ["## Source defect", ""]
    lines += [f"- 文章 {key[0]} ・ {key[1]} ・「{spans[key]}」：{issue}（Judge {'、'.join(sorted(judges))}）"
              for key, (issue, judges) in sorted(found.items())] or ["沒有 Judge 發現原文的問題。"]
    return lines + [""]


def judge_settings_lines(experiment: Experiment, out: OutputDir) -> list[str]:
    return header_lines(experiment, out.read_judge_calls()) if experiment.judges else []


def judge_lines(experiment: Experiment, out: OutputDir, records: list[RunRecord], rows: list[ComponentResult], articles, decisions: Decisions) -> list[str]:
    """Judge 的結果：每個 Approach × Direction × 輸入版本 × Judge 一節，加上 Source defect 清單。"""
    calls = out.read_judge_calls()
    if not experiment.judges or not calls:
        return []
    limits = experiment.config.judging.mqm_run_limit
    # mqm_run_limit 刻意沒有評的 Run 不算「尚未評完」：不在這一節
    ok_records = [r for r in records if r.outcome.value == "ok" and not (r.approach in limits and r.run_index > limits[r.approach])]
    translations = {RunKey.of(r): out.read_translation(r) for r in ok_records}
    calls_by_judge_run = defaultdict(lambda: defaultdict(list))
    for call in calls:
        calls_by_judge_run[call.judge][RunKey.of(call)].append(call)
    rows_by_run = defaultdict(list)
    for row in rows:
        rows_by_run[RunKey.of(row)].append(row)
    lines = ["## Judge 評核", ""]
    groups = sorted({(r.approach, r.direction, r.input_variant) for r in ok_records})
    for judge in experiment.judges:
        for approach, direction, variant in groups:
            members = [r for r in ok_records if (r.approach, r.direction, r.input_variant) == (approach, direction, variant)]
            if not any(RunKey.of(r) in calls_by_judge_run[judge.id] for r in members):
                continue
            score = score_group(members, articles, translations, calls_by_judge_run[judge.id], rows_by_run, experiment.config.weights, judge.id, decisions)
            tag = "" if variant is InputVariant.FULL else f"（{variant}）"
            lines += group_lines(f"### {approach} × {direction}{tag} × Judge {judge.id}（{judge.config.role}）", score, experiment.config.weights)
    return lines + source_defect_lines(calls)
