"""報告的「Judge 實驗」一節（Q31）：角色為 experiment 的 Judge（Qwen）是不是比較容易漏看中國負面新聞被淡化的地方。

這一節只作實驗，**不影響 Gate 和分數**：Gate、分數、審閱清單都只用 primary 角色的 Judge。每個 experiment 的 Judge 對 primary 的 Judge（這一組的主 Judge，
Gemini）比較三件事，全部只列數字，判斷門檻不寫死：
  比較 1（合成變體）：校準結果裡，兩個 Judge 在 political 與 general 的變體上，失敗的事實的 recall，以及差距。變體與被測模型無關，沒有「Qwen 判自己」的問題。
  比較 2（真實譯文）：同一批 ok 的 Run 的譯文，兩個 Judge 各判一次。主 Judge 判為 Weakened 或 Absent、experiment 的 Judge 判為 Present 的事實，是「疑似漏看」；
    按分組（Gate 的分組：domain，或實驗設定的覆寫分組；參考的主 Judge 也按分組取，與 Gate 一致）和譯文來源（與這個 Judge 同家族的 Approach 的譯文＝自己的譯文，其他 Approach 與 baseline 的譯文＝其他）
    分開統計。單位是「事實 × Run」：同一條事實在幾個 Run 就算幾次，包括 main-only 的 Run。分母是主 Judge 判為失敗、而且
    experiment 的 Judge 有最終 verdict（兩次一致）的事實；experiment 的 Judge uncertain、無效、Unjudged 或還沒評完的，另外列，不混進「漏看」。
    疑似漏看的事實列出位置（文章、Component、事實的種類），原文、譯文和兩個 Judge 的判定寫在 experiment-judge-misses.md（含原文，只在本機看），供人手抽查。
  比較 3（盲抽）：有交叉檢驗的結果時，列出對照模型抽到而盲抽的模型沒抽到的句子數；內容在 crosscheck.md。盲抽的模型被擋、失敗或還沒有結果的文章不算進句數，另列。
另外單獨列 experiment 的 Judge 的 Unjudged 率（兩次請求都失敗，含輸入端被擋；MQM、事實、全文篩查三個任務，按分組，並列主 Judge 作對照）和無效 Finding 率：「根本不給評」本身就是結果，
與「評了但漏看」分開。
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from .articles import apply_variant
from .calibration import JudgeEvaluation, metrics_for
from .config import Experiment, PlannedJudge
from .consistency import Status, judge_component
from .crosscheck import component_texts, current_calls, reference_gaps
from .facts import FactStatus, Pending, judge_facts
from .ground_truth import GroundTruth
from .judging import same_family
from .pairs import component_pairs
from .report_text import ratio, shown
from .schemas import (FAILING_VERDICTS, WHOLE_RUN, Article, Domain, GoldCategory, InvalidKind, JudgeRole, Outcome, RunKey, RunRecord, Task, Verdict)
from .screening import judge_screening
from .store import OutputDir
from .textrules import one_line

NO_GROUP = "（沒有 ground truth）"
MAX_LISTED_MISSES = 20


class Source(StrEnum):
    """譯文來源：與這個 Judge 同家族的 Approach 的譯文是「自己的」；其他 Approach 和 baseline 是「其他」；Approach 已不在實驗設定內的另列。"""
    OWN = "自己的譯文"
    OTHER = "其他 Approach 或 baseline 的譯文"
    UNKNOWN = "Approach 不在實驗設定內的譯文"


class Relation(StrEnum):
    """一條事實，兩個 Judge 的結果的關係。"""
    MISSED = "missed"               # 主 Judge 判失敗、experiment 的 Judge 判 Present：疑似漏看
    BOTH_FAILED = "both_failed"
    OVER_REPORTED = "over_reported"   # experiment 的 Judge 判失敗、主 Judge 判 Present
    BOTH_PRESENT = "both_present"
    EXPERIMENT_NOT_FINAL = "experiment_not_final"   # 主 Judge 判失敗，experiment 的 Judge 沒有最終 verdict
    PRESENT_NOT_FINAL = "present_not_final"         # 主 Judge 判 Present，experiment 的 Judge 沒有最終 verdict
    PRIMARY_NOT_FINAL = "primary_not_final"         # 主 Judge 沒有最終 verdict：不比


@dataclass
class PairTally:
    """一個 experiment 的 Judge 對主 Judge，在某個分組與譯文來源上的比較。"""
    reference: str = ""
    counts: Counter = field(default_factory=Counter)             # Relation → 事實數
    experiment_not_final: Counter = field(default_factory=Counter)   # experiment 的 Judge 沒有最終 verdict 的原因

    @property
    def comparable(self) -> int:
        """主 Judge 判失敗、experiment 的 Judge 有最終 verdict 的事實數：「疑似漏看」的分母。"""
        return self.counts[Relation.MISSED] + self.counts[Relation.BOTH_FAILED]


@dataclass
class JudgeHealth:
    """一個 Judge 在一個分組的 Unjudged 與無效：按任務。「根本不給評」本身就是結果，與「評了但漏看」分開。"""
    done: Counter = field(default_factory=Counter)        # 任務 → 跑完（兩個請求都有結果，含 Unjudged）的單位數：MQM 與事實是 Component，篩查是 Run
    unjudged: Counter = field(default_factory=Counter)    # 任務 → 其中 Unjudged 的
    failures: Counter = field(default_factory=Counter)    # Unjudged 的請求的結果類別
    block_reasons: Counter = field(default_factory=Counter)   # block 的原因碼（輸入端與輸出端的前綴不同）
    valid_findings: int = 0
    invalid_findings: int = 0
    valid_verdicts: int = 0
    invalid_verdicts: int = 0


@dataclass(frozen=True)
class Miss:
    where: str          # 文章、Component、事實的種類
    detail: list[str]   # 原文、譯文和兩個 Judge 的判定（只寫進本機的檔案）


@dataclass
class Gathered:
    tallies: dict = field(default_factory=lambda: defaultdict(PairTally))     # (experiment 的 Judge id, 分組, Source) → PairTally
    misses: dict = field(default_factory=lambda: defaultdict(list))           # (experiment 的 Judge id, 分組) → [Miss]
    health: dict = field(default_factory=lambda: defaultdict(JudgeHealth))    # (Judge id, 分組) → JudgeHealth
    groups_without_reference: set = field(default_factory=set)                # 沒有主 Judge、沒辦法比的分組


def fact_view(judgment, key: str) -> Verdict | Pending:
    """一條事實在一個 Judge 那裡的狀態：最終 verdict，或還沒有最終 verdict 的原因。"""
    if judgment.status is Status.UNJUDGED:
        return Pending.UNJUDGED
    if judgment.status is Status.INCOMPLETE:
        return Pending.INCOMPLETE
    result = judgment.facts[key]
    return result.verdict if result.status is FactStatus.FINAL else Pending(result.status.value)


def classify(primary: Verdict | Pending, experimental: Verdict | Pending) -> Relation:
    """兩個 Judge 對同一條事實的狀態的關係。主 Judge 沒有最終 verdict 的不比。"""
    if not isinstance(primary, Verdict):
        return Relation.PRIMARY_NOT_FINAL
    if primary in FAILING_VERDICTS:
        if experimental is Verdict.PRESENT:
            return Relation.MISSED
        return Relation.BOTH_FAILED if experimental in FAILING_VERDICTS else Relation.EXPERIMENT_NOT_FINAL
    if experimental in FAILING_VERDICTS:
        return Relation.OVER_REPORTED
    return Relation.BOTH_PRESENT if experimental is Verdict.PRESENT else Relation.PRESENT_NOT_FINAL


def _verdict_text(calls, key: str) -> str:
    """一個 Judge 對一條事實兩次的 verdict、引用和理由，一行。"""
    parts = []
    for call in sorted(calls, key=lambda c: c.call_index):
        verdict = next((v for v in call.verdicts if v.fact == key), None)
        parts.append(f"第 {call.call_index} 次 " + (f"{verdict.verdict.value}" + (f"，引用「{one_line(verdict.quote)}」" if verdict.quote else "") + f"，理由：{one_line(verdict.reason)}"
                                                   if verdict else f"沒有 verdict（{call.outcome.value}）"))
    return "；".join(parts) or "沒有請求紀錄"


def _source(experiment: Experiment, record: RunRecord, experimental: PlannedJudge) -> Source:
    approach = experiment.approach_config(record.approach)
    if record.is_baseline:
        return Source.OTHER
    if approach is None:
        return Source.UNKNOWN
    return Source.OWN if same_family(approach.family, experimental.config.family) else Source.OTHER


class Collector:
    """一次走過所有 ok 的 Run，累積 Gathered。把「這個 Judge 的健康」與「兩個 Judge 的事實比較」分開，各自一個方法。"""

    def __init__(self, experiment: Experiment, out: OutputDir, articles: dict[str, Article], truth: GroundTruth | None):
        self.experiment, self.articles, self.truth, self.out = experiment, articles, truth, out
        self.experiments = [j for j in experiment.judges if j.config.role is JudgeRole.EXPERIMENT]
        self.calls = {task: defaultdict(list) for task in Task}
        for task in Task:
            for call in out.read_judge_calls(task):
                self.calls[task][(call.judge, RunKey.of(call), call.component_id)].append(call)
        self.gathered = Gathered()

    def run(self, records: list[RunRecord]) -> Gathered:
        for record in (r for r in records if r.outcome is Outcome.OK):
            self._record(record)
        return self.gathered

    def _record(self, record: RunRecord) -> None:
        run, truth = RunKey.of(record), self.truth
        group = truth.group_of(record.article_id) if truth and record.article_id in truth.domains else NO_GROUP
        reference = self.experiment.primary_judge_for(group)[0]
        pairs = component_pairs(apply_variant(self.articles[record.article_id], record.input_variant), self.out.read_translation(record))
        in_mqm_scope = (limit := self.experiment.config.judging.mqm_run_limit.get(record.approach)) is None or record.run_index <= limit
        for judge in [*self.experiments, *([reference] if reference else [])]:
            health = self.gathered.health[(judge.id, group)]
            for pair in (p for p in pairs if p.translated_text is not None):
                if in_mqm_scope:
                    self._mqm_health(health, judge, run, pair)
                if truth is not None and truth.sentences_in(record.article_id, pair.id):
                    self._fact_health(health, judge, run, pair)
            self._screen_health(health, judge, run, pairs)
        if truth is None or group == NO_GROUP:
            return
        if reference is None:
            self.gathered.groups_without_reference.add(group)
            return
        for pair in (p for p in pairs if p.translated_text is not None):   # 漏段的事實是程式判的 Absent，不是 Judge 的判定
            sentences = truth.sentences_in(record.article_id, pair.id)
            if sentences:
                self._compare_facts(record, run, group, reference, pair, sentences)

    def _note_unjudged(self, health: JudgeHealth, task: Task, calls: list, failed) -> None:
        health.unjudged[task] += 1
        health.failures.update(o.value for o in failed)
        health.block_reasons.update(c.reason or "（沒有寫）" for c in calls if c.outcome is Outcome.BLOCK)

    def _mqm_health(self, health: JudgeHealth, judge: PlannedJudge, run: RunKey, pair) -> None:
        calls = self.calls[Task.MQM].get((judge.id, run, pair.id), [])
        judgment = judge_component(calls, pair)
        health.done[Task.MQM] += judgment.status is not Status.INCOMPLETE
        if judgment.status is Status.UNJUDGED:
            self._note_unjudged(health, Task.MQM, calls, judgment.failed)
        health.valid_findings += sum(len(c.findings) for c in calls)
        health.invalid_findings += sum(1 for c in calls for i in c.invalid if i.kind is InvalidKind.FINDING)

    def _fact_health(self, health: JudgeHealth, judge: PlannedJudge, run: RunKey, pair) -> None:
        calls = self.calls[Task.FACT].get((judge.id, run, pair.id), [])
        judgment = judge_facts(calls, self.truth.sentences_in(run.article_id, pair.id))
        health.done[Task.FACT] += judgment.status is not Status.INCOMPLETE
        if judgment.status is Status.UNJUDGED:
            self._note_unjudged(health, Task.FACT, calls, judgment.failed)
        health.valid_verdicts += sum(len(c.verdicts) for c in calls)
        health.invalid_verdicts += sum(1 for c in calls for i in c.invalid if i.kind is InvalidKind.VERDICT)

    def _screen_health(self, health: JudgeHealth, judge: PlannedJudge, run: RunKey, pairs) -> None:
        calls = self.calls[Task.SCREEN].get((judge.id, run, WHOLE_RUN), [])
        judgment = judge_screening(calls, pairs)
        health.done[Task.SCREEN] += judgment.status is not Status.INCOMPLETE
        if judgment.status is Status.UNJUDGED:
            self._note_unjudged(health, Task.SCREEN, calls, judgment.failed)

    def _compare_facts(self, record: RunRecord, run: RunKey, group: str, reference: PlannedJudge, pair, sentences) -> None:
        judged = {j.id: judge_facts(self.calls[Task.FACT].get((j.id, run, pair.id), []), sentences) for j in [reference, *self.experiments]}
        for experimental in self.experiments:
            tally = self.gathered.tallies[(experimental.id, group, _source(self.experiment, record, experimental))]
            tally.reference = reference.id
            for fact in (f for s in sentences for f in s.facts):
                primary_view, experiment_view = fact_view(judged[reference.id], fact.key), fact_view(judged[experimental.id], fact.key)
                relation = classify(primary_view, experiment_view)
                tally.counts[relation] += 1
                if relation is Relation.EXPERIMENT_NOT_FINAL:
                    tally.experiment_not_final[experiment_view] += 1
                if relation is Relation.MISSED:
                    self.gathered.misses[(experimental.id, group)].append(self._miss(record, run, pair, fact, reference, experimental))

    def _miss(self, record: RunRecord, run: RunKey, pair, fact, reference: PlannedJudge, experimental: PlannedJudge) -> Miss:
        shown_calls = lambda judge: _verdict_text(self.calls[Task.FACT].get((judge.id, run, pair.id), []), fact.key)   # noqa: E731
        return Miss(f"文章 {record.article_id} ・ {pair.id} ・ {fact.kind.value}",
                    [f"### `{record.article_id}` ・ `{pair.id}` ・ {run.label}・事實（{fact.kind.value}）：{one_line(fact.text)}", "",
                     f"- 原文：{one_line(pair.source_text)}", f"- 譯文：{one_line(pair.translated_text)}",
                     f"- Judge {reference.id}（主）：{shown_calls(reference)}", f"- Judge {experimental.id}（experiment）：{shown_calls(experimental)}", ""])


def gather_experiment(experiment: Experiment, out: OutputDir, records: list[RunRecord], articles: dict[str, Article], truth: GroundTruth | None) -> Gathered | None:
    """走過所有 ok 的 Run 累積結果。沒有 experiment 角色的 Judge 就沒有這一節：回傳 None。"""
    if not any(j.config.role is JudgeRole.EXPERIMENT for j in experiment.judges):
        return None
    return Collector(experiment, out, articles, truth).run(records)


def misses_text(gathered: Gathered | None) -> str:
    """疑似漏看的詳細清單（含原文與譯文，只寫在 data/，不放進報告）。沒有 experiment 的 Judge 或沒有疑似漏看就是空的：呼叫端要刪掉舊的檔案。"""
    if gathered is None or not gathered.misses:
        return ""
    lines = ["# 疑似漏看（主 Judge 判 Weakened 或 Absent、experiment 的 Judge 判 Present）", "", "含原文與譯文，只在本機看。供人手抽查：先看原文與譯文，再看兩個 Judge 各自的引用與理由。", ""]
    for _, found in sorted(gathered.misses.items()):
        lines += [line for miss in found for line in miss.detail]
    return "\n".join(lines).rstrip("\n") + "\n"


# ───────────── 報告 ─────────────

def _percent_gap(first: tuple[int, int], second: tuple[int, int]) -> str:
    """first 的比率減 second 的比率，單位是百分點；任何一邊的分母是 0 就不能比。"""
    if not first[1] or not second[1]:
        return "不能比（有一邊沒有資料）"
    return f"{100 * (first[0] / first[1] - second[0] / second[1]):+.1f} 個百分點"


def _variant_recall(evaluation: JudgeEvaluation | None, domain: Domain) -> tuple[tuple[int, int], str]:
    """(失敗的事實的 recall 的分子分母, 這個數字的但書)。Unjudged 算沒有抓到（在分母裡）；uncertain、尚未評完、過期的請求另外說，免得兩邊的項目不一樣卻看不出來。"""
    if evaluation is None:
        return (0, 0), ""
    m = metrics_for(evaluation, domain=domain, category=GoldCategory.VARIANT)
    caveats = [f"{label} {n}" for label, n in (("Judge 被擋的項目（算沒有抓到）", m.fact_unjudged), ("uncertain 的事實（算沒有抓到）", m.facts_uncertain),
                                              ("尚未評完的項目", m.fact_incomplete), ("過期而忽略的請求", evaluation.stale)) if n]
    return (m.failing_found, m.failing_expected), (f"（{'、'.join(caveats)}）" if caveats else "")


def _comparison_1(experiment: Experiment, experimental: PlannedJudge, evaluations: dict[str, JudgeEvaluation]) -> list[str]:
    """校準結果裡，兩個 Judge 在 political 與 general 的變體上，失敗的事實的 recall。"""
    lines = ["#### 比較 1：合成變體的 recall（來自校準；只列數字）", ""]
    if not any(e.has_records for e in evaluations.values()):
        return lines + ["還沒有校準結果。", ""]
    recalls = {}
    for domain in (Domain.CHINA_POLITICAL, Domain.GENERAL):
        ref = experiment.primary_judge_for(domain.value)[0]
        (theirs, their_caveats), (ours, our_caveats) = (_variant_recall(evaluations.get(ref.id) if ref else None, domain), _variant_recall(evaluations.get(experimental.id), domain))
        recalls[domain] = (theirs, ours)
        lines.append(f"- {domain.value} 的變體：" + (f"Judge {ref.id}（主）{ratio(*theirs)}{their_caveats}；" if ref else "沒有主 Judge；") + f"Judge {experimental.id} {ratio(*ours)}{our_caveats}；"
                     f"差（{experimental.id} − {ref.id if ref else '主 Judge'}）{_percent_gap(ours, theirs)}")
    (p_ref, p_exp), (g_ref, g_exp) = recalls[Domain.CHINA_POLITICAL], recalls[Domain.GENERAL]
    lines.append(f"- political − general：Judge {experimental.id} {_percent_gap(p_exp, g_exp)}；主 Judge {_percent_gap(p_ref, g_ref)}")
    return lines + ["- Judge 被擋（Unjudged）的變體算沒有抓到，分母不變；完整的指標在 calibration.md。", ""]


def _comparison_2(experimental: PlannedJudge, gathered: Gathered, truth: GroundTruth | None) -> list[str]:
    lines = ["#### 比較 2：真實譯文，兩個 Judge 各判一次（疑似漏看 = 主 Judge 判 Weakened 或 Absent、這個 Judge 判 Present）", "",
             "單位是「事實 × Run」：同一條事實在幾個 Run 就算幾次（包括 main-only 的 Run）；漏段的事實是程式判的 Absent，不在內。", ""]
    if truth is None:
        return lines + ["沒有 ground truth，沒有事實可以比。", ""]
    keys = sorted(k for k in gathered.tallies if k[0] == experimental.id)
    for _, group, source in keys:
        t = gathered.tallies[(experimental.id, group, source)]
        c, pending = t.counts, "、".join(f"{Pending(view).label} {n}" for view, n in sorted(t.experiment_not_final.items())) or "無"
        lines.append(f"- {group} ・ {source.value}（主 Judge {t.reference}）：疑似漏看 {ratio(c[Relation.MISSED], t.comparable)}；兩邊都判失敗 {c[Relation.BOTH_FAILED]}；"
                     f"主 Judge 判失敗而這個 Judge 沒有最終 verdict：{pending}；這個 Judge 判失敗而主 Judge 判 Present：{c[Relation.OVER_REPORTED]}；兩邊都判 Present：{c[Relation.BOTH_PRESENT]}；"
                     f"主 Judge 判 Present 而這個 Judge 沒有最終 verdict：{c[Relation.PRESENT_NOT_FINAL]}；主 Judge 沒有最終 verdict（不比）：{c[Relation.PRIMARY_NOT_FINAL]}")
    lines += [f"- {group}：沒有主 Judge，沒辦法比" for group in sorted(gathered.groups_without_reference)]
    if not keys and not gathered.groups_without_reference:
        lines.append("還沒有兩個 Judge 都判過的事實。")
    for (judge_id, group), found in sorted(gathered.misses.items()):
        if judge_id == experimental.id:
            lines.append(f"- {group} 的疑似漏看（{len(found)} 條；原文、譯文和兩個 Judge 的判定在 experiment-judge-misses.md）：" + "、".join(m.where for m in found[:MAX_LISTED_MISSES])
                         + (f"…另有 {len(found) - MAX_LISTED_MISSES} 條" if len(found) > MAX_LISTED_MISSES else ""))
    return lines + [""]


def _health_line(judge: PlannedJudge, group: str, h: JudgeHealth, summary: bool) -> str:
    failures = "、".join(f"{o} {n}" for o, n in sorted(h.failures.items())) or "無"
    reasons = "、".join(f"{shown(r, summary)} {n}" for r, n in sorted(h.block_reasons.items())) or "無"
    rate = lambda task: ratio(h.unjudged[task], h.done[task])   # noqa: E731
    return (f"- Judge {judge.id}（{judge.config.role}）・ {group}：MQM Unjudged {rate(Task.MQM)}（Component）；事實 Unjudged {rate(Task.FACT)}（Component）；全文篩查 Unjudged {rate(Task.SCREEN)}（Run）"
            f"（Unjudged 的請求結果：{failures}；block 的原因：{reasons}）；無效 Finding 率 {ratio(h.invalid_findings, h.valid_findings + h.invalid_findings)}；"
            f"無效的 verdict {ratio(h.invalid_verdicts, h.valid_verdicts + h.invalid_verdicts)}")


def _health(experiment: Experiment, experimental: PlannedJudge, gathered: Gathered, summary: bool) -> list[str]:
    lines = ["#### Unjudged 率與無效 Finding 率（與「評了但漏看」分開；並列主 Judge 作對照）", ""]
    judges = {j.id: j for j in experiment.judges}
    rows = [_health_line(judges[judge_id], group, h, summary) for (judge_id, group), h in sorted(gathered.health.items())
            if judge_id == experimental.id or judges[judge_id].config.role is JudgeRole.PRIMARY]
    return lines + (rows or ["還沒有 Judge 紀錄。"]) + [""]


def _comparison_3(experiment: Experiment, out: OutputDir, articles: dict[str, Article], truth: GroundTruth | None) -> list[str]:
    cfg = experiment.config.crosscheck
    lines = ["#### 比較 3：盲抽（內容在 crosscheck.md，含原文，只在本機看）", ""]
    if cfg is None or truth is None or cfg.reference_judge is None:
        return lines + ["沒有設定交叉檢驗（crosscheck）的對照模型。", ""]
    fresh = current_calls(experiment, truth, articles, out.read_crosscheck_calls())
    compared = gaps = failed = pending = reference_missing = 0
    for article_id in articles:
        if article_id not in truth.domains or truth.domains[article_id] is not Domain.CHINA_POLITICAL:
            continue
        blind, reference = fresh.get((article_id, cfg.judge)), fresh.get((article_id, cfg.reference_judge))
        if reference is None or reference.outcome is not Outcome.OK:
            reference_missing += 1
        elif blind is None:
            pending += 1
        elif blind.outcome is not Outcome.OK:
            failed += 1   # 盲抽的模型被擋或失敗：這是它自己的結果，不是「漏看」，句數不計
        else:
            compared += 1
            gaps += len(reference_gaps(component_texts(articles[article_id]), reference.sentences, blind.sentences))
    if not (compared or failed or pending or reference_missing):
        return lines + ["還沒有 china-political 文章的交叉檢驗結果。", ""]
    return lines + [f"- china-political：兩個模型都有結果的 {compared} 篇，對照模型抽到而盲抽的模型沒抽到的句子 {gaps} 句；"
                    f"盲抽的模型被擋或失敗 {failed} 篇、還沒有結果 {pending} 篇（這些文章不算進句數，與「漏看」分開）；對照模型沒有結果 {reference_missing} 篇。只作參考。", ""]


def experiment_lines(experiment: Experiment, gathered: Gathered | None, out: OutputDir, articles: dict[str, Article], truth: GroundTruth | None,
                     evaluations: dict[str, JudgeEvaluation], *, summary: bool = False) -> list[str]:
    """報告的「Judge 實驗」一節。沒有 experiment 角色的 Judge（gathered 是 None）就沒有這一節。"""
    if gathered is None:
        return []
    lines = ["## Judge 實驗（Q31）", "",
             "角色為 experiment 的 Judge 只作實驗：**不影響 Gate、分數和審閱清單**，結果單獨列在這裡。判斷門檻（例如 recall 差多少算有問題）不寫死，只列數字。", ""]
    for experimental in (j for j in experiment.judges if j.config.role is JudgeRole.EXPERIMENT):
        lines += [f"### Judge {experimental.id}（experiment；家族 {experimental.config.family}）", "", *_comparison_1(experiment, experimental, evaluations),
                  *_comparison_2(experimental, gathered, truth), *_health(experiment, experimental, gathered, summary), *_comparison_3(experiment, out, articles, truth)]
    return lines
