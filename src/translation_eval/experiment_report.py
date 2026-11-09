"""報告的「Judge 實驗」一節（Q31）：角色為 experiment 的 Judge（Qwen）是不是比較容易漏看中國負面新聞被淡化的地方。

這一節只作實驗，**不影響 Gate 和分數**：Gate、分數、審閱清單都只用 primary 角色的 Judge。每個 experiment 的 Judge 對 primary 的 Judge（這一組的主 Judge，
Gemini）比較三件事，全部只列數字，判斷門檻不寫死：
  比較 1（合成變體）：校準結果裡，兩個 Judge 在 political 與 general 的變體上，失敗的事實的 recall，以及差距。變體與被測模型無關，沒有「Qwen 判自己」的問題。
  比較 2（真實譯文）：同一批 ok 的 Run 的譯文，兩個 Judge 各判一次。主 Judge 判為 Weakened 或 Absent、experiment 的 Judge 判為 Present 的事實，是「疑似漏看」；
    按 domain 和譯文來源（與這個 Judge 同家族的 Approach 的譯文＝自己的譯文，其他 Approach 與 baseline 的譯文＝其他）分開統計。分母是主 Judge 判為失敗、而且
    experiment 的 Judge 有最終 verdict（兩次一致）的事實；experiment 的 Judge uncertain、無效、Unjudged 或還沒評完的，另外列，不混進「漏看」。
    疑似漏看的事實列出位置（文章、Component、事實的種類），原文、譯文和兩個 Judge 的判定寫在 experiment-judge-misses.md（含原文，只在本機看），供人手抽查。
  比較 3（盲抽）：有交叉檢驗的結果時，列出對照模型抽到而盲抽的模型沒抽到的句子數；內容在 crosscheck.md。
另外單獨列 experiment 的 Judge 的 Unjudged 率（兩次請求都失敗，含輸入端被擋；按任務與 domain，並列主 Judge 作對照）和無效 Finding 率：「根本不給評」本身就是結果，
與「評了但漏看」分開。
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .articles import apply_variant
from .calibration import JudgeEvaluation, metrics_for
from .config import Experiment, PlannedJudge
from .consistency import Status, judge_component
from .crosscheck import component_texts, current_calls, reference_gaps
from .facts import FactStatus, judge_facts
from .ground_truth import GroundTruth
from .judge_report import ratio
from .judging import same_family
from .pairs import component_pairs
from .report_text import shown
from .schemas import FAILING_VERDICTS, Article, Domain, GoldCategory, JudgeRole, Outcome, RunKey, RunRecord, Verdict
from .store import OutputDir

NO_DOMAIN = "（沒有 ground truth）"
OWN, OTHER = "自己的譯文", "其他模型的譯文"


@dataclass
class PairTally:
    """一個 experiment 的 Judge 對主 Judge，在某個 domain 與譯文來源上的比較。"""
    missed: int = 0                 # 主 Judge 判失敗、experiment 的 Judge 判 Present：疑似漏看
    both_failed: int = 0
    over_reported: int = 0          # experiment 的 Judge 判失敗、主 Judge 判 Present
    both_present: int = 0
    not_final: Counter = field(default_factory=Counter)   # 主 Judge 判失敗、experiment 的 Judge 沒有最終 verdict：原因

    @property
    def comparable(self) -> int:
        """主 Judge 判失敗、experiment 的 Judge 有最終 verdict 的事實數：「疑似漏看」的分母。"""
        return self.missed + self.both_failed


@dataclass
class JudgeHealth:
    """一個 Judge 的 Unjudged 與無效：按任務。「根本不給評」本身就是結果，與「評了但漏看」分開。"""
    mqm_done: int = 0
    mqm_unjudged: int = 0
    fact_done: int = 0
    fact_unjudged: int = 0
    failures: Counter = field(default_factory=Counter)        # Unjudged 的請求的結果類別
    block_reasons: Counter = field(default_factory=Counter)   # block 的原因碼
    valid_findings: int = 0
    invalid_findings: int = 0
    invalid_verdicts: int = 0


def _label(judgment, key: str) -> str:
    """一條事實的 Judge 結果：Present、Weakened、Absent（最終 verdict），或 uncertain、invalid、unjudged、incomplete。"""
    if judgment.status is Status.UNJUDGED:
        return "unjudged"
    if judgment.status is Status.INCOMPLETE:
        return "incomplete"
    result = judgment.facts[key]
    return result.verdict.value if result.status is FactStatus.FINAL else result.status.value


def _classify(p_label: str, e_label: str, tally: PairTally) -> bool:
    """把一條事實的兩個結果記進統計；回傳是不是「疑似漏看」。主 Judge 沒有最終 verdict 的不比。"""
    p_fails = p_label in {v.value for v in FAILING_VERDICTS}
    if p_label != Verdict.PRESENT.value and not p_fails:
        return False
    e_fails, e_present = e_label in {v.value for v in FAILING_VERDICTS}, e_label == Verdict.PRESENT.value
    if p_fails and e_present:
        tally.missed += 1
        return True
    if p_fails and e_fails:
        tally.both_failed += 1
    elif p_fails:
        tally.not_final[e_label] += 1
    elif e_fails:
        tally.over_reported += 1
    elif e_present:
        tally.both_present += 1
    return False


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _verdict_text(calls, key: str) -> str:
    """一個 Judge 對一條事實兩次的 verdict、引用和理由，一行。"""
    parts = []
    for call in sorted(calls, key=lambda c: c.call_index):
        verdict = next((v for v in call.verdicts if v.fact == key), None)
        parts.append(f"第 {call.call_index} 次 " + (f"{verdict.verdict.value}" + (f"，引用「{_one_line(verdict.quote)}」" if verdict.quote else "") + f"，理由：{_one_line(verdict.reason)}"
                                                   if verdict else f"沒有 verdict（{call.outcome.value}）"))
    return "；".join(parts) or "沒有請求紀錄"


@dataclass
class Gathered:
    tallies: dict = field(default_factory=lambda: defaultdict(PairTally))     # (experiment judge id, domain, source) → PairTally
    misses: dict = field(default_factory=lambda: defaultdict(list))           # (experiment judge id, domain) → [(位置, 詳細)]
    health: dict = field(default_factory=lambda: defaultdict(JudgeHealth))    # (judge id, domain) → JudgeHealth


def _gather(experiment: Experiment, out: OutputDir, records: list[RunRecord], articles: dict[str, Article], truth: GroundTruth | None) -> Gathered:
    experiments = [j for j in experiment.judges if j.config.role is JudgeRole.EXPERIMENT]
    mqm, facts = defaultdict(list), defaultdict(list)
    for call in out.read_judge_calls("mqm"):
        mqm[(call.judge, RunKey.of(call), call.component_id)].append(call)
    for call in out.read_judge_calls("fact"):
        facts[(call.judge, RunKey.of(call), call.component_id)].append(call)
    limits = experiment.config.judging.mqm_run_limit
    gathered = Gathered()
    for record in (r for r in records if r.outcome is Outcome.OK):
        run, article = RunKey.of(record), articles[record.article_id]
        domain = truth.domains[record.article_id] if truth and record.article_id in truth.domains else None
        domain_name = domain.value if domain else NO_DOMAIN
        reference = experiment.primary_judge_for(domain.value)[0] if domain else next((j for j in experiment.judges if j.config.role is JudgeRole.PRIMARY), None)
        pairs = component_pairs(apply_variant(article, record.input_variant), out.read_translation(record))
        approach = experiment.approach_config(record.approach)
        for judge in [*experiments, *([reference] if reference else [])]:
            health = gathered.health[(judge.id, domain_name)]
            for pair in pairs:
                if pair.translated_text is None:
                    continue
                if record.approach not in limits or record.run_index <= limits[record.approach]:
                    calls = mqm.get((judge.id, run, pair.id), [])
                    judgment = judge_component(calls, pair)
                    health.mqm_done += judgment.status is not Status.INCOMPLETE
                    health.mqm_unjudged += judgment.status is Status.UNJUDGED
                    if judgment.status is Status.UNJUDGED:
                        health.failures.update(o.value for o in judgment.failed)
                        health.block_reasons.update(c.reason or "（沒有寫）" for c in calls if c.outcome is Outcome.BLOCK)
                    health.valid_findings += sum(len(c.findings) for c in calls)
                    health.invalid_findings += sum(1 for c in calls for i in c.invalid if i.kind == "finding")
        if truth is None or domain is None or reference is None:
            continue
        for pair in pairs:
            sentences = truth.sentences_in(record.article_id, pair.id)
            if not sentences or pair.translated_text is None:   # 漏段的事實是程式判的 Absent，不是 Judge 的判定
                continue
            by_judge = {j.id: judge_facts(facts.get((j.id, run, pair.id), []), sentences) for j in [reference, *experiments]}
            for judge in [reference, *experiments]:
                health = gathered.health[(judge.id, domain_name)]
                health.fact_done += by_judge[judge.id].status is not Status.INCOMPLETE
                health.fact_unjudged += by_judge[judge.id].status is Status.UNJUDGED
                if by_judge[judge.id].status is Status.UNJUDGED:
                    health.failures.update(o.value for o in by_judge[judge.id].failed)
                    health.block_reasons.update(c.reason or "（沒有寫）" for c in facts.get((judge.id, run, pair.id), []) if c.outcome is Outcome.BLOCK)
                health.invalid_verdicts += sum(1 for c in facts.get((judge.id, run, pair.id), []) for i in c.invalid if i.kind == "verdict")
            for experimental in experiments:
                source = OWN if approach is not None and same_family(approach.family, experimental.config.family) else OTHER
                tally = gathered.tallies[(experimental.id, domain_name, source)]
                for sentence in sentences:
                    for fact in sentence.facts:
                        p_label, e_label = _label(by_judge[reference.id], fact.key), _label(by_judge[experimental.id], fact.key)
                        if _classify(p_label, e_label, tally):
                            detail = [f"### `{record.article_id}` ・ `{pair.id}` ・ {run.label}・事實（{fact.kind.value}）：{_one_line(fact.text)}", "",
                                      f"- 原文：{_one_line(pair.source_text)}", f"- 譯文：{_one_line(pair.translated_text)}",
                                      f"- Judge {reference.id}（主）：{_verdict_text(facts.get((reference.id, run, pair.id), []), fact.key)}",
                                      f"- Judge {experimental.id}（experiment）：{_verdict_text(facts.get((experimental.id, run, pair.id), []), fact.key)}", ""]
                            gathered.misses[(experimental.id, domain_name)].append((f"文章 {record.article_id} ・ {pair.id} ・ {fact.kind.value}", detail))
    return gathered


def _percent_gap(first: tuple[int, int], second: tuple[int, int]) -> str:
    """first 的比率減 second 的比率，單位是百分點；任何一邊的分母是 0 就不能比。"""
    if not first[1] or not second[1]:
        return "不能比（有一邊沒有資料）"
    return f"{100 * (first[0] / first[1] - second[0] / second[1]):+.1f} 個百分點"


def _recall(evaluation: JudgeEvaluation | None, domain: Domain) -> tuple[int, int]:
    if evaluation is None:
        return 0, 0
    m = metrics_for(evaluation, domain=domain, category=GoldCategory.VARIANT)
    return m.failing_found, m.failing_expected


def _comparison_1(experiment: Experiment, experimental: PlannedJudge, evaluations: dict[str, JudgeEvaluation]) -> list[str]:
    """校準結果裡，兩個 Judge 在 political 與 general 的變體上，失敗的事實的 recall。"""
    lines = ["#### 比較 1：合成變體的 recall（來自校準；只列數字）", ""]
    if not any(e.has_records for e in evaluations.values()):
        return lines + ["還沒有校準結果。", ""]
    reference = {d: experiment.primary_judge_for(d.value)[0] for d in Domain}
    recalls = {}
    for domain in (Domain.CHINA_POLITICAL, Domain.GENERAL):
        ref = reference[domain]
        theirs, ours = _recall(evaluations.get(ref.id) if ref else None, domain), _recall(evaluations.get(experimental.id), domain)
        recalls[domain] = (theirs, ours)
        lines.append(f"- {domain.value} 的變體：" + (f"Judge {ref.id}（主）{ratio(*theirs)}；" if ref else "沒有主 Judge；") + f"Judge {experimental.id} {ratio(*ours)}；"
                     f"差（{experimental.id} − {ref.id if ref else '主 Judge'}）{_percent_gap(ours, theirs)}")
    (p_ref, p_exp), (g_ref, g_exp) = recalls[Domain.CHINA_POLITICAL], recalls[Domain.GENERAL]
    lines.append(f"- political − general：Judge {experimental.id} {_percent_gap(p_exp, g_exp)}；主 Judge {_percent_gap(p_ref, g_ref)}")
    return lines + ["- Judge 被擋（Unjudged）的變體算沒有抓到，分母不變；完整的指標在 calibration.md。", ""]


def _comparison_2(experimental: PlannedJudge, gathered: Gathered, truth: GroundTruth | None) -> list[str]:
    lines = ["#### 比較 2：真實譯文，兩個 Judge 各判一次（疑似漏看 = 主 Judge 判 Weakened 或 Absent、這個 Judge 判 Present）", ""]
    if truth is None:
        return lines + ["沒有 ground truth，沒有事實可以比。", ""]
    keys = sorted(k for k in gathered.tallies if k[0] == experimental.id)
    for _, domain, source in keys:
        t = gathered.tallies[(experimental.id, domain, source)]
        pending = "、".join(f"{reason} {n}" for reason, n in sorted(t.not_final.items())) or "無"
        lines.append(f"- {domain} ・ {source}：疑似漏看 {ratio(t.missed, t.comparable)}；兩邊都判失敗 {t.both_failed}；主 Judge 判失敗而這個 Judge 沒有最終 verdict：{pending}；"
                     f"這個 Judge 判失敗而主 Judge 判 Present：{t.over_reported}；兩邊都判 Present：{t.both_present}")
    if not keys:
        lines.append("還沒有兩個 Judge 都判過的事實。")
    for (judge_id, domain), found in sorted(gathered.misses.items()):
        if judge_id == experimental.id:
            lines.append(f"- {domain} 的疑似漏看（{len(found)} 條；原文、譯文和兩個 Judge 的判定在 experiment-judge-misses.md）：" + "、".join(where for where, _ in found[:20])
                         + (f"…另有 {len(found) - 20} 條" if len(found) > 20 else ""))
    return lines + [""]


def _health_line(judge: PlannedJudge, domain: str, h: JudgeHealth, summary: bool) -> str:
    failures = "、".join(f"{o} {n}" for o, n in sorted(h.failures.items())) or "無"
    reasons = "、".join(f"{shown(r, summary)} {n}" for r, n in sorted(h.block_reasons.items())) or "無"
    return (f"- Judge {judge.id}（{judge.config.role}）・ {domain}：MQM Unjudged {ratio(h.mqm_unjudged, h.mqm_done)}；事實 Unjudged {ratio(h.fact_unjudged, h.fact_done)}"
            f"（Unjudged 的請求結果：{failures}；block 的原因：{reasons}）；無效 Finding 率 {ratio(h.invalid_findings, h.valid_findings + h.invalid_findings)}；無效的 verdict {h.invalid_verdicts}")


def _health(experiment: Experiment, experimental: PlannedJudge, gathered: Gathered, summary: bool) -> list[str]:
    lines = ["#### Unjudged 率與無效 Finding 率（與「評了但漏看」分開；並列主 Judge 作對照）", ""]
    judges = {j.id: j for j in experiment.judges}
    shown_any = False
    for (judge_id, domain), h in sorted(gathered.health.items()):
        if judge_id == experimental.id or (judge_id in judges and judges[judge_id].config.role is JudgeRole.PRIMARY):
            lines.append(_health_line(judges[judge_id], domain, h, summary))
            shown_any = True
    return lines + ([] if shown_any else ["還沒有 Judge 紀錄。"]) + [""]


def _comparison_3(experiment: Experiment, out: OutputDir, articles: dict[str, Article], truth: GroundTruth | None) -> list[str]:
    cfg = experiment.config.crosscheck
    lines = ["#### 比較 3：盲抽（內容在 crosscheck.md，含原文，只在本機看）", ""]
    if cfg is None or truth is None or cfg.reference_judge is None:
        return lines + ["沒有設定交叉檢驗（crosscheck）的對照模型。", ""]
    fresh = current_calls(experiment, truth, articles, out.read_crosscheck_calls())
    gaps = blocked = ran = 0
    for article_id in articles:
        if article_id not in truth.domains or truth.domains[article_id] is not Domain.CHINA_POLITICAL:
            continue
        blind, reference = fresh.get((article_id, cfg.judge)), fresh.get((article_id, cfg.reference_judge))
        if reference is None or reference.outcome is not Outcome.OK:
            continue
        ran += 1
        blind_ok = blind is not None and blind.outcome is Outcome.OK
        blocked += blind is not None and blind.outcome is not Outcome.OK
        gaps += len(reference_gaps(component_texts(articles[article_id]), reference.sentences, blind.sentences if blind_ok else []))
    if not ran:
        return lines + ["還沒有 china-political 文章的對照結果。", ""]
    return lines + [f"- china-political {ran} 篇有對照結果：對照模型抽到而盲抽的模型沒抽到的句子 {gaps} 句；盲抽的模型本身被擋或失敗 {blocked} 篇（與「漏看」分開）。只作參考。", ""]


def experiment_lines(experiment: Experiment, out: OutputDir, records: list[RunRecord], articles: dict[str, Article], truth: GroundTruth | None,
                     evaluations: dict[str, JudgeEvaluation], *, summary: bool = False) -> tuple[list[str], str]:
    """(報告的行, 疑似漏看的詳細清單)。沒有 experiment 角色的 Judge 就沒有這一節。詳細清單含原文與譯文，只寫在 data/，不放進報告。"""
    experiments = [j for j in experiment.judges if j.config.role is JudgeRole.EXPERIMENT]
    if not experiments:
        return [], ""
    gathered = _gather(experiment, out, records, articles, truth)
    lines = ["## Judge 實驗（Q31）", "",
             "角色為 experiment 的 Judge 只作實驗：**不影響 Gate、分數和審閱清單**，結果單獨列在這裡。判斷門檻（例如 recall 差多少算有問題）不寫死，只列數字。", ""]
    misses = ["# 疑似漏看（主 Judge 判 Weakened 或 Absent、experiment 的 Judge 判 Present）", "", "含原文與譯文，只在本機看。供人手抽查：先看原文與譯文，再看兩個 Judge 各自的引用與理由。", ""]
    for experimental in experiments:
        lines += [f"### Judge {experimental.id}（experiment；家族 {experimental.config.family}）", "", *_comparison_1(experiment, experimental, evaluations),
                  *_comparison_2(experimental, gathered, truth), *_health(experiment, experimental, gathered, summary), *_comparison_3(experiment, out, articles, truth)]
        for (judge_id, _), found in sorted(gathered.misses.items()):
            if judge_id == experimental.id:
                misses += [line for _, detail in found for line in detail]
    return lines, "\n".join(misses).rstrip("\n") + "\n"
