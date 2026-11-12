"""Judge 評核過的實驗（展示頁的 --judged-in，通常是抽樣實驗）：讀出展示頁要用的東西。
「淡化」一節（showcase）和「被寫輕的例子」一節（showcase-text）都從這裡讀，審閱決定的檢查（過期就停止）也只有這一處。

要有 judging、ground truth 和事實核對的紀錄，而且已經 check。
"""
from dataclasses import dataclass

from .articles import load_article
from .check import require_checked
from .config import Experiment, load_experiment
from .errors import InputError
from .ground_truth import GroundTruth, load_ground_truth
from .review_decisions import Decisions, check_decisions, load_decisions
from .review_items import ReviewItem, build_items
from .schemas import RunRecord, Task
from .store import OutputDir


@dataclass(frozen=True)
class Judged:
    experiment: Experiment
    out: OutputDir
    records: list[RunRecord]
    articles: dict
    truth: GroundTruth
    decisions: Decisions
    items: list[ReviewItem]


def load_judged(config_path: str, data_dir: str) -> Judged:
    experiment = load_experiment(config_path)
    cfg = experiment.config
    if not experiment.judges:
        raise InputError("--judged-in 的實驗沒有設定 Judge")
    if cfg.ground_truth is None:
        raise InputError("--judged-in 的實驗沒有 ground truth：淡化核對的事實來自 ground truth")
    out = OutputDir(data_dir, cfg.id)
    records = out.require_runs()
    if not out.read_judge_calls(Task.FACT):
        raise InputError("--judged-in 的實驗還沒有 Judge 的事實核對紀錄：先執行 judge")
    rows = out.read_components()
    require_checked(experiment, out, records, rows)
    decisions = load_decisions(out)
    articles = {i: load_article(cfg.articles.dir, i) for i in cfg.articles.ids}
    truth = load_ground_truth(cfg.ground_truth, articles)
    items = build_items(experiment, out, records, rows, articles, truth)
    check_decisions(decisions, items)   # 決定過期或指向不存在的項目：停止，與報告一樣
    return Judged(experiment, out, records, articles, truth, decisions, items)
