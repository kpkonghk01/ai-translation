"""讀入並驗證實驗設定與 Approach 設定。任何一項不合法都丟出 InputError，由指令在寫出任何東西之前以非 0 結束。

設定檔裡的路徑都相對於目前的工作目錄（指令一律在 repo 根目錄執行）。
"""
import hashlib
import json
from dataclasses import dataclass

from .errors import InputError
from .loading import read_model
from .schemas import ApproachConfig, ExperimentConfig
from .terms import LoadedTermList, load_term_list


@dataclass(frozen=True)
class PlannedApproach:
    """實驗裡的一個 Approach：設定、每篇文章每個 Direction 的 Run 次數，以及設定的 hash。"""
    config: ApproachConfig
    runs: int
    config_hash: str

    @property
    def id(self) -> str:
        return self.config.id


@dataclass(frozen=True)
class Experiment:
    config: ExperimentConfig
    approaches: list[PlannedApproach]
    term_list: LoadedTermList | None


def config_hash(approach: ApproachConfig) -> str:
    canonical = json.dumps(approach.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_experiment(path: str) -> Experiment:
    config = read_model(path, ExperimentConfig)
    approaches, seen, problems = [], set(), []
    for entry in config.approaches:
        approach = read_model(entry.config, ApproachConfig)
        if approach.id in seen:
            problems.append(f"Approach id 重覆：{approach.id}")
            continue
        seen.add(approach.id)
        approaches.append(PlannedApproach(approach, entry.runs, config_hash(approach)))
    if problems:
        raise InputError(problems)
    term_list = load_term_list(config.term_list) if config.term_list else None
    return Experiment(config=config, approaches=approaches, term_list=term_list)
