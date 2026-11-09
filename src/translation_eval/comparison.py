"""報告的「與 baseline 比較」一節：每個 Approach 的 MQM 分數對 baseline 的分數（spec 第 7 節）。

質素的 baseline 是實驗設定的 comparison.baseline（方案 A）；某個 Direction 另外要比的 baseline 寫在 comparison.also_against（繁→簡另外與
方案 E 比）。baseline 自己不與自己比；baseline 與 baseline 之間（例如 E 對 A）也列出一次。每個 Judge（role 為 primary）各比一次。
只比較 full 的 Run，而且只在這三種文章上比：
  - baseline 這個 Direction 的所有 Run 都有紀錄而且都是 ok（baseline 被 Block 或有失敗的文章不比：否則比的就不是同一批文章）；
  - 被比的 Approach 至少有一個 ok 的 Run（沒有譯文的文章沒有分數；它的 Block 在 Block 率呈報，不在這裡當作零分）；
  - 兩邊的 ok 的 Run 都評完了（有 Component 還沒評完的，分數是缺的）。
多個 Run 時，每篇文章取 ok 的 Run 的平均（總罰分）；分母是這些文章的原文字數（不計空白），每千字的罰分 = 平均總罰分的合計 ÷ 原文字數 × 1000。
Unjudged 的 Component 沒有 Judge 的 Finding，兩邊的數量並列，免得「沒有被抓到」被當成「沒有問題」。
"""
from dataclasses import dataclass

from .config import Experiment
from .gate_report import planned_runs
from .judge_report import JudgeContext
from .schemas import InputVariant, Outcome, RunKey, RunRecord

MAX_IDS = 10


@dataclass
class ArticleScore:
    """一篇文章在一邊（Approach 或 baseline）的分數：ok 的 Run 的平均。"""
    main_consistent: float
    main_all: float
    peripheral_consistent: float
    peripheral_all: float
    main_chars: int
    peripheral_chars: int
    unjudged: float   # 平均每個 Run 有幾個 Component 是 Unjudged


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def side_score(ctx: JudgeContext, judge_id: str, runs: list[RunRecord]) -> ArticleScore | None:
    """None：有 Component 還沒評完。"""
    scores = [ctx.score([r], judge_id) for r in runs]
    if any(s.incomplete for s in scores):
        return None
    return ArticleScore(
        _mean([s.main.penalty_consistent for s in scores]), _mean([s.main.penalty_all for s in scores]),
        _mean([s.peripheral.penalty_consistent for s in scores]), _mean([s.peripheral.penalty_all for s in scores]),
        scores[0].main.chars, scores[0].peripheral.chars, _mean([s.unjudged for s in scores]))


def _ids(article_ids: list[str]) -> str:
    shown = "、".join(article_ids[:MAX_IDS])
    return f"{len(article_ids)} 篇" + (f"（{shown}" + ("…" if len(article_ids) > MAX_IDS else "") + "）" if article_ids else "")


def _number(value: float) -> str:
    return f"{value:.1f}"


def _per_1000(penalty: float, chars: int) -> float:
    return penalty / chars * 1000 if chars else 0.0


def _gap(candidate: float, reference: float) -> str:
    return f"{candidate - reference:+.1f}"


def references_for(experiment: Experiment, direction) -> list[str]:
    comparison = experiment.config.comparison
    return [comparison.baseline, *comparison.also_against.get(direction, [])]


def _label(experiment: Experiment, reference: str) -> str:
    return "質素 baseline" if reference == experiment.config.comparison.baseline else "baseline"


def comparison_lines(experiment: Experiment, ctx: JudgeContext | None, records: list[RunRecord]) -> list[str]:
    """沒有設 comparison、或沒有可以算分數的 Judge 紀錄時沒有這一節。"""
    comparison = experiment.config.comparison
    if comparison is None or ctx is None:
        return []
    full = [r for r in records if r.input_variant is InputVariant.FULL]
    in_scope = {RunKey.of(r) for r in ctx.records}   # MQM 要評的 ok 的 Run
    candidates = [i for i in [*(p.id for p in experiment.approaches), *(b.id for b in experiment.config.baselines)] if i != comparison.baseline]
    runs_in = lambda who, direction: planned_runs(experiment, who, direction, InputVariant.FULL) is not None   # noqa: E731  這個 Direction 有它的 Run
    lines = ["## 與 baseline 比較", "",
             "只比較 full 的 Run；每篇文章取 ok 的 Run 的平均；差 = 被比的 Approach − baseline，正數表示罰分較多（質素較差）。"
             "baseline 所有 Run 都是 ok、被比的 Approach 至少有一個 ok 的 Run、兩邊都評完的文章才比較。", ""]
    judges = [j for j in experiment.judges if j.config.role.value == "primary"]
    for direction in experiment.config.directions:
        for candidate in candidates:
            for reference in dict.fromkeys(references_for(experiment, direction)):
                if reference == candidate or not runs_in(candidate, direction) or not runs_in(reference, direction):
                    continue
                for judge in judges:
                    lines += _section(experiment, ctx, full, in_scope, judge, candidate, reference, direction)
    return lines


def _section(experiment, ctx, full, in_scope, judge, candidate, reference, direction) -> list[str]:
    def runs_of(approach: str, article_id: str) -> list[RunRecord]:
        return [r for r in full if (r.approach, r.direction, r.article_id) == (approach, direction, article_id)]

    article_ids = [a for a in experiment.config.articles.ids if runs_of(candidate, a) or runs_of(reference, a)]
    compared, reference_not_ok, no_ok_run, unfinished = [], [], [], []
    scores = {}
    for article_id in article_ids:
        reference_runs, candidate_runs = runs_of(reference, article_id), runs_of(candidate, article_id)
        planned = planned_runs(experiment, reference, direction, InputVariant.FULL)
        if not reference_runs or any(r.outcome is not Outcome.OK for r in reference_runs) or (planned is not None and len(reference_runs) != planned):
            reference_not_ok.append(article_id)
            continue
        ok_runs = [r for r in candidate_runs if r.outcome is Outcome.OK and RunKey.of(r) in in_scope]
        if not ok_runs:
            no_ok_run.append(article_id)
            continue
        sides = (side_score(ctx, judge.id, [r for r in reference_runs if RunKey.of(r) in in_scope]), side_score(ctx, judge.id, ok_runs))
        if None in sides or not [r for r in reference_runs if RunKey.of(r) in in_scope]:
            unfinished.append(article_id)
            continue
        compared.append(article_id)
        scores[article_id] = sides
    heading = f"### {candidate} × {direction} 對 {reference}（{_label(experiment, reference)}）× Judge {judge.id}"
    lines = [heading, "", f"- 比較的文章：{_ids(compared)}"]
    skipped = [(f"{reference} 沒有全部 ok 的 Run", reference_not_ok), (f"{candidate} 沒有 ok 的 Run", no_ok_run), ("尚未評完", unfinished)]
    lines.append("- 沒有比較的文章：" + "；".join(f"{why} {_ids(ids)}" for why, ids in skipped if ids) if any(ids for _, ids in skipped) else "- 沒有比較的文章：無")
    if not compared:
        return lines + ["- 沒有可以比較的文章", ""]
    pairs = list(scores.values())
    for name, chars_of, consistent_of, all_of in (("Main", lambda s: s.main_chars, lambda s: s.main_consistent, lambda s: s.main_all),
                                                    ("Peripheral", lambda s: s.peripheral_chars, lambda s: s.peripheral_consistent, lambda s: s.peripheral_all)):
        chars = sum(chars_of(reference_side) for reference_side, _ in pairs)
        totals = {label: (sum(of(c) for _, c in pairs), sum(of(r) for r, _ in pairs)) for label, of in (("consistent", consistent_of), ("all", all_of))}
        per_1000 = {label: (_per_1000(c, chars), _per_1000(r, chars)) for label, (c, r) in totals.items()}
        lines.append(
            f"- {name}：每千字原文的罰分（只計一致的 Finding／連 uncertain 一起計）：{candidate} {_number(per_1000['consistent'][0])}／{_number(per_1000['all'][0])}、"
            f"{reference} {_number(per_1000['consistent'][1])}／{_number(per_1000['all'][1])}、"
            f"差 {_gap(*per_1000['consistent'])}／{_gap(*per_1000['all'])}（原文 {chars} 字）")
        lines.append(
            f"- {name}：平均每個 Run 的總罰分：{candidate} {_number(totals['consistent'][0])}／{_number(totals['all'][0])}、"
            f"{reference} {_number(totals['consistent'][1])}／{_number(totals['all'][1])}")
    lines.append(f"- Unjudged 的 Component（平均每個 Run）：{candidate} {_number(sum(c.unjudged for _, c in pairs))}、"
                 f"{reference} {_number(sum(r.unjudged for r, _ in pairs))}")
    return lines + [""]
