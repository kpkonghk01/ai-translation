"""讀入並驗證實驗設定與 Approach 設定。任何一項不合法都丟出 InputError，由指令在寫出任何東西之前以非 0 結束。

設定檔裡的路徑都相對於目前的工作目錄（指令一律在 repo 根目錄執行）。
"""
import hashlib
import json
from dataclasses import dataclass

from .errors import InputError
from .loading import read_model
from .schemas import ApproachConfig, Direction, ExperimentConfig, InputVariant, JudgeConfig, LengthRatioRange
from .terms import LoadedTermList, load_term_list


@dataclass(frozen=True)
class PlannedApproach:
    """實驗裡的一個 Approach：設定、每篇文章每個 Direction 每個輸入版本的 Run 次數，以及設定的 hash。"""
    config: ApproachConfig
    runs: int
    config_hash: str
    input_variants: tuple[InputVariant, ...]

    @property
    def id(self) -> str:
        return self.config.id


@dataclass(frozen=True)
class PlannedJudge:
    config: JudgeConfig
    config_hash: str

    @property
    def id(self) -> str:
        return self.config.id


@dataclass(frozen=True)
class Experiment:
    config: ExperimentConfig
    approaches: list[PlannedApproach]
    term_list: LoadedTermList
    judges: list[PlannedJudge]

    def approach_config(self, approach_id: str) -> ApproachConfig | None:
        """LLM Approach 的設定；baseline（不經 API）沒有。"""
        return next((p.config for p in self.approaches if p.id == approach_id), None)


def config_hash(approach: ApproachConfig) -> str:
    canonical = json.dumps(approach.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# 譯文與原文長度之比的預設範圍：暫定，等有繁→英的實際分佈再定；實驗設定的 length_ratio 可以按 Direction 覆寫
DEFAULT_LENGTH_RATIO = {
    Direction.TRAD_TO_EN: LengthRatioRange(min=1.5, max=8.0),
    Direction.TRAD_TO_SIMPLIFIED: LengthRatioRange(min=0.6, max=1.6),
}


def length_ranges(config: ExperimentConfig) -> dict[Direction, LengthRatioRange]:
    """實際使用的長度比例範圍：預設值加上實驗設定的覆寫。"""
    return {**DEFAULT_LENGTH_RATIO, **config.length_ratio}


def load_experiment(path: str) -> Experiment:
    config = read_model(path, ExperimentConfig)
    approaches, seen, problems = [], set(), []
    for entry in config.approaches:
        approach = read_model(entry.config, ApproachConfig)
        if approach.id in seen:
            problems.append(f"Approach id 重覆：{approach.id}")
            continue
        seen.add(approach.id)
        approaches.append(PlannedApproach(approach, entry.runs, config_hash(approach), tuple(entry.input_variants)))
    clashes = [b.id for b in config.baselines if b.id in seen]
    problems += [f"baseline 的 id 與 Approach 的 id 重覆：{i}" for i in clashes]
    judges = load_judges(config, seen, problems)
    if problems:
        raise InputError(problems)
    term_list = load_term_list(config.term_list) if config.term_list else LoadedTermList.unused()
    return Experiment(config=config, approaches=approaches, term_list=term_list, judges=judges)


def load_judges(config: ExperimentConfig, approach_ids: set[str], problems: list[str]) -> list[PlannedJudge]:
    if config.judging is None:
        return []
    judges, seen = [], set()
    for entry in config.judging.judges:
        judge = read_model(entry.config, JudgeConfig)
        if judge.id in seen:
            problems.append(f"Judge id 重覆：{judge.id}")
        elif judge.id in approach_ids:
            problems.append(f"Judge 的 id 與 Approach 的 id 重覆：{judge.id}")
        else:
            seen.add(judge.id)
            judges.append(PlannedJudge(judge, config_hash(judge)))
    for exception in config.judging.same_family_exceptions:
        if exception.approach not in approach_ids or exception.judge not in seen:
            problems.append(f"同家族的例外指向不存在的 Approach 或 Judge：{exception.approach} × {exception.judge}")
    for approach_id in config.judging.mqm_run_limit:
        if approach_id not in approach_ids:
            problems.append(f"mqm_run_limit 指向不存在的 Approach：{approach_id}")
    return judges
