"""報告的「與 baseline 比較」一節：每個 Approach 的 MQM 分數對 baseline 的分數（spec 第 7 節）。

質素的 baseline 是實驗設定的 comparison.baseline（方案 A）；某個 Direction 另外要比的 baseline 寫在 comparison.also_against（繁→簡另外與
方案 E 比）。每個 Approach 和每個匯入的 baseline 都與這個 Direction 的 baseline 比，自己不與自己比（所以 A 對 E 與 E 對 A 都會列：
各自以被當作 baseline 的那一邊篩選文章）。每個 Judge（role 為 primary）各比一次。只比較 full 的 Run，而且只在這三種文章上比：
  - baseline 這個 Direction 的所有 Run 都有紀錄而且都是 ok（baseline 被 Block 或有失敗的文章不比：否則比的就不是同一批文章）；
  - 被比的一邊至少有一個可評核的 ok 的 Run（沒有譯文的文章沒有分數；它的 Block 在 Block 率呈報，不在這裡當作零分）；
  - 兩邊可評核的 ok 的 Run 都評完了（有 Component 還沒評完的，分數是缺的）。
多個 Run 時，每篇文章取 ok 的 Run 的平均（總罰分）；分母是這些文章的原文字數（不計空白），每千字的罰分 = 平均總罰分的合計 ÷ 原文字數 × 1000。
被比的一邊有 Run 沒有 ok 或還沒有紀錄時，平均只用手上的 ok 的 Run：用了幾個 Run、計畫幾個一併列出，不夠就標示。
Unjudged 的 Component 沒有 Judge 的 Finding，兩邊的數量並列，免得「沒有被抓到」被當成「沒有問題」。
"""
from dataclasses import dataclass, field

from .config import Experiment, PlannedJudge
from .gate_report import planned_runs
from .judge_report import JudgeContext
from .schemas import Direction, InputVariant, JudgeRole, Outcome, RunRecord


@dataclass(frozen=True)
class RoleMean:
    """Main 或 Peripheral 在一篇文章上的分數：ok 的 Run 的平均總罰分（兩個版本），和原文字數。"""
    consistent: float   # 只計一致的 Finding
    everything: float   # 連 uncertain 一起計
    chars: int


@dataclass(frozen=True)
class ArticleScore:
    main: RoleMean
    peripheral: RoleMean
    unjudged: float   # 平均每個 Run 有幾個 Component 是 Unjudged
    runs: int         # 平均用了幾個 Run


@dataclass
class Selection:
    compared: dict[str, tuple[ArticleScore, ArticleScore]] = field(default_factory=dict)   # 文章 id → (baseline, 被比的一邊)
    baseline_not_ok: list[str] = field(default_factory=list)
    no_scorable_run: list[str] = field(default_factory=list)
    unfinished: list[str] = field(default_factory=list)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def side_score(ctx: JudgeContext, judge_id: str, runs: list[RunRecord]) -> ArticleScore | None:
    """runs 是可評核的 ok 的 Run（至少一個）。None：有 Component 還沒評完。"""
    scores = [ctx.score([r], judge_id) for r in runs]
    if any(s.incomplete for s in scores):
        return None
    role = lambda name: RoleMean(_mean([getattr(s, name).penalty_consistent for s in scores]),   # noqa: E731
                                 _mean([getattr(s, name).penalty_all for s in scores]), getattr(scores[0], name).chars)
    return ArticleScore(role("main"), role("peripheral"), _mean([s.unjudged for s in scores]), len(runs))


def scorable_runs(ctx: JudgeContext, records: list[RunRecord]) -> list[RunRecord]:
    return [r for r in records if r.outcome is Outcome.OK and ctx.covers(r)]


def select_articles(experiment: Experiment, ctx: JudgeContext, full: list[RunRecord], judge_id: str, candidate: str, baseline: str,
                    direction: Direction) -> Selection:
    def runs_of(approach: str, article_id: str) -> list[RunRecord]:
        return [r for r in full if (r.approach, r.direction, r.article_id) == (approach, direction, article_id)]

    planned = planned_runs(experiment, baseline, direction, InputVariant.FULL)
    selection = Selection()
    for article_id in experiment.config.articles.ids:
        baseline_runs, candidate_runs = runs_of(baseline, article_id), runs_of(candidate, article_id)
        if not baseline_runs and not candidate_runs:
            continue   # 兩邊都沒有紀錄：還沒翻譯，不是「沒有比較」
        if not baseline_runs or any(r.outcome is not Outcome.OK for r in baseline_runs) or (planned is not None and len(baseline_runs) < planned):
            selection.baseline_not_ok.append(article_id)
            continue
        baseline_scorable, candidate_scorable = scorable_runs(ctx, baseline_runs), scorable_runs(ctx, candidate_runs)
        if not candidate_scorable or not baseline_scorable:
            selection.no_scorable_run.append(article_id)
            continue
        sides = (side_score(ctx, judge_id, baseline_scorable), side_score(ctx, judge_id, candidate_scorable))
        if None in sides:
            selection.unfinished.append(article_id)
        else:
            selection.compared[article_id] = sides
    return selection


def _ids(article_ids) -> str:
    article_ids = list(article_ids)
    return f"{len(article_ids)} 篇" + (f"（{'、'.join(article_ids)}）" if article_ids else "")


def _one_decimal(value: float) -> str:
    return f"{value:.1f}"


def _gap(candidate: float, baseline: float) -> str:
    return f"{candidate - baseline:+.1f}"


def baselines_for(experiment: Experiment, direction: Direction) -> list[str]:
    comparison = experiment.config.comparison
    return list(dict.fromkeys([comparison.baseline, *comparison.also_against.get(direction, [])]))


def scored_runs_planned(experiment: Experiment, who: str, direction: Direction) -> int:
    """這個 Approach（或 baseline）每篇文章在 MQM 評核範圍內計畫幾個 Run。"""
    planned = planned_runs(experiment, who, direction, InputVariant.FULL) or 0
    limit = experiment.config.judging.mqm_run_limit.get(who) if experiment.config.judging else None
    return planned if limit is None else min(planned, limit)


def comparison_lines(experiment: Experiment, ctx: JudgeContext | None, records: list[RunRecord]) -> list[str]:
    """沒有設 comparison、或沒有可以算分數的 Judge 紀錄時沒有這一節。"""
    if experiment.config.comparison is None:
        return []
    if ctx is None:   # 設了 comparison 卻沒有分數可比：說出來，不要整節消失
        return ["## 與 baseline 比較", "", "還沒有 Judge 的 MQM 紀錄，沒有分數可以比較：先執行 judge。", ""]
    full = [r for r in records if r.input_variant is InputVariant.FULL]
    everyone = [*(p.id for p in experiment.approaches), *(b.id for b in experiment.config.baselines)]
    lines = ["## 與 baseline 比較", "",
             "只比較 full 的 Run；每篇文章取 ok 的 Run 的平均；差 = 被比的一邊 − baseline，正數表示罰分較多（質素較差）。"
             "baseline 所有 Run 都是 ok、被比的一邊至少有一個可評核的 ok 的 Run、兩邊都評完的文章才比較。", ""]
    judges = [j for j in experiment.judges if j.config.role is JudgeRole.PRIMARY]
    for direction in experiment.config.directions:
        for baseline in baselines_for(experiment, direction):
            for candidate in everyone:
                if candidate == baseline or not planned_runs(experiment, candidate, direction, InputVariant.FULL):
                    continue   # 自己不與自己比；這個 Direction 沒有 Run 的（例如 E 的繁→英）不列
                for judge in judges:
                    lines += _section(experiment, ctx, full, judge, candidate, baseline, direction)
    return lines


def _role_lines(label: str, role: str, candidate: str, baseline: str, pairs: list[tuple[ArticleScore, ArticleScore]]) -> list[str]:
    """Main 或 Peripheral 的兩行：每千字的罰分與差，和平均每個 Run 的總罰分。pairs 的每一項是 (baseline, 被比的一邊)。"""
    BASELINE, CANDIDATE = 0, 1
    chars = sum(getattr(pair[BASELINE], role).chars for pair in pairs)
    total = lambda side, version: sum(getattr(getattr(pair[side], role), version) for pair in pairs)   # noqa: E731
    per_1000 = lambda side, version: total(side, version) / chars * 1000 if chars else 0.0   # noqa: E731

    def both(side, measure):
        return f"{_one_decimal(measure(side, 'consistent'))}／{_one_decimal(measure(side, 'everything'))}"

    gap = "／".join(_gap(per_1000(CANDIDATE, v), per_1000(BASELINE, v)) for v in ("consistent", "everything"))
    return [
        f"- {label}：每千字原文的罰分（只計一致的 Finding／連 uncertain 一起計）：{candidate} {both(CANDIDATE, per_1000)}、"
        f"{baseline} {both(BASELINE, per_1000)}、差 {gap}（原文 {chars} 字）",
        f"- {label}：平均每個 Run 的總罰分：{candidate} {both(CANDIDATE, total)}、{baseline} {both(BASELINE, total)}",
    ]


def _section(experiment: Experiment, ctx: JudgeContext, full: list[RunRecord], judge: PlannedJudge, candidate: str, baseline: str,
             direction: Direction) -> list[str]:
    selection = select_articles(experiment, ctx, full, judge.id, candidate, baseline, direction)
    kind = "質素 baseline" if baseline == experiment.config.comparison.baseline else "baseline"
    lines = [f"### {candidate} × {direction} 對 {baseline}（{kind}）× Judge {judge.id}", "", f"- 比較的文章：{_ids(selection.compared)}"]
    skipped = [(f"{baseline} 沒有全部 ok 的 Run", selection.baseline_not_ok), (f"{candidate} 沒有可評核的 ok 的 Run", selection.no_scorable_run),
               ("尚未評完", selection.unfinished)]
    listed = [f"{why} {_ids(ids)}" for why, ids in skipped if ids]
    lines.append("- 沒有比較的文章：" + ("；".join(listed) if listed else "無"))
    if not selection.compared:
        return lines + ["- 沒有可以比較的文章", ""]
    pairs = list(selection.compared.values())
    lines += _role_lines("Main", "main", candidate, baseline, pairs) + _role_lines("Peripheral", "peripheral", candidate, baseline, pairs)
    used = sum(c.runs for _, c in pairs)
    planned = scored_runs_planned(experiment, candidate, direction) * len(pairs)
    lines.append(f"- 平均用的 Run：{candidate} {used} 個（這些文章計畫 {planned} 個）" + ("；⚠ 有 Run 沒有 ok 或還沒有紀錄，平均不完整" if used < planned else "")
                 + f"、{baseline} {sum(b.runs for b, _ in pairs)} 個")
    lines.append(f"- Unjudged 的 Component（平均每個 Run）：{candidate} {_one_decimal(sum(c.unjudged for _, c in pairs))}、"
                 f"{baseline} {_one_decimal(sum(b.unjudged for b, _ in pairs))}")
    return lines + [""]
