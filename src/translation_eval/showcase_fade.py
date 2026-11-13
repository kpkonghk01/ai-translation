"""展示報告的「淡化」一節要用的數字：從 Judge 評核過的實驗（通常是抽樣實驗）讀出每個模型、每個方向、每個題材組的淡化率，
和（有校準的話）Judge 在已知答案上的準確度。只有計數，沒有文章內容。

用法（showcase 的選項）：
  --judged-in <實驗設定>       Judge 評核過的實驗（要有 judging、ground_truth 和事實核對的紀錄，而且已經 check）
  --calibrated-in <實驗設定>   Judge 校準過的實驗（要有 calibration）；沒有就寫「準確度還沒有量度」

數字與報告的 Gate 一節同一份（gate_results）：審閱的決定也已經套用，所以人審之後再執行一次，數字就更新。
每個模型另有一欄「人已審的失敗項」：Judge 判為被寫輕或不見的事實有幾項已有人審決定。有的模型審過、有的沒審，數字不能直接比，頁面要讓讀的人看得出來。
只看完整文章。報告的 Gate 失敗、被擋與空內容的 Run 不在分母的規則照舊。
"""
from collections import Counter
from dataclasses import dataclass

from .articles import load_article
from .calibration import evaluate_judge, metrics_for
from .config import load_experiment
from .errors import InputError
from .gate_report import gate_results, rate_of
from .gold import load_gold_set
from .ground_truth import load_ground_truth
from .judged import load_judged
from .report_text import ordered_groups
from .review_items import ItemStatus
from .schemas import Direction, InputVariant
from .stats import clopper_pearson_upper
from .store import OutputDir


@dataclass(frozen=True)
class Accuracy:
    """Judge 在 Gold set 上：預期失敗的事實抓到幾個；預期 Present 的事實被誤判成失敗幾個。"""
    found: int
    expected: int
    false_alarms: int
    present: int


@dataclass(frozen=True)
class Cell:
    failed: int       # 被淡化的不重覆句子數
    evaluated: int    # 有結果的不重覆句子數；0 就是沒有結果
    upper: float | None   # 單尾 95% 上限；沒有結果時是 None


@dataclass(frozen=True)
class Row:
    name: str
    cells: dict[tuple[Direction, str], Cell]   # (方向, 組) → 格
    failed: int    # Judge 判為被寫輕或不見的事實（一句可以有幾條，所以和格子裡的句子數不同）
    decided: int   # 其中已經有人審決定的


@dataclass(frozen=True)
class Fade:
    rows: list[Row]
    directions: list[Direction]
    groups: list[str]
    articles_by_group: Counter
    judge_name: str
    must: int
    decided: int
    accuracy: Accuracy | None


def accuracy_of(config_path: str, data_dir: str, judge_id: str) -> Accuracy | None:
    """校準實驗裡這個 Judge 的準確度。沒有校準結果（或 Gold set 在這一格沒有預期失敗的事實）就是 None。"""
    experiment = load_experiment(config_path)
    cfg = experiment.config
    if cfg.calibration is None:
        raise InputError("--calibrated-in 的實驗沒有設定 calibration")
    judge = next((j for j in experiment.judges if j.id == judge_id), None)
    if judge is None:
        raise InputError(f"--calibrated-in 的實驗沒有 Judge {judge_id}（要和 --judged-in 的主 Judge 同一個 id）")
    articles = {i: load_article(cfg.articles.dir, i) for i in cfg.articles.ids}
    truth = load_ground_truth(cfg.ground_truth, articles) if cfg.ground_truth else None
    gold = load_gold_set(cfg.calibration.gold_set, truth, articles)
    evaluation = evaluate_judge(gold, truth, OutputDir(data_dir, cfg.id).read_gold_calls(), judge)
    m = metrics_for(evaluation)
    if not evaluation.has_records or not m.failing_expected:
        return None
    return Accuracy(m.failing_found, m.failing_expected, m.false_alarms, m.present_final)


def load_fade(args) -> Fade:
    judged = load_judged(args.judged_in, args.data_dir)
    experiment, truth, decisions = judged.experiment, judged.truth, judged.decisions
    cfg = experiment.config
    must = [i for i in judged.items if i.must_see]
    results = [r for r in gate_results(experiment, judged.out, judged.records, judged.articles, truth, decisions)
               if r.variant is InputVariant.FULL and r.gate is not None]
    names = experiment.display_names()
    cells = {}
    for r in results:
        failed, evaluated = rate_of(r.gate)
        cells.setdefault(r.approach, {})[(r.direction, r.group)] = Cell(failed, evaluated, clopper_pearson_upper(failed, evaluated) if evaluated else None)
    order = [p.id for p in experiment.approaches] + [b.id for b in cfg.baselines]
    judge = next(r.judge for r in results)
    with_sentences = {s.article_id for s in truth.sentences}
    accuracy = accuracy_of(args.calibrated_in, args.data_dir, judge.id) if args.calibrated_in else None
    failed = [i for i in judged.items if i.status is ItemStatus.FAILED and i.run.input_variant is InputVariant.FULL]   # FAILED 只有事實項目才有
    return Fade(rows=[Row(names[a], cells[a], sum(1 for i in failed if i.run.approach == a),
                          sum(1 for i in failed if i.run.approach == a and decisions.get(i.key))) for a in order if a in cells],
                directions=[d for d in cfg.directions if any(r.direction is d for r in results)],
                groups=ordered_groups(r.group for r in results),
                articles_by_group=Counter(truth.group_of(a) for a in with_sentences),
                judge_name=judge.config.display_name or judge.config.model,
                must=len(must), decided=sum(1 for i in must if decisions.get(i.key)), accuracy=accuracy)
