"""讀入並驗證實驗設定與 Approach 設定。任何一項不合法都丟出 InputError，由指令在寫出任何東西之前以非 0 結束。

設定檔裡的路徑都相對於目前的工作目錄（指令一律在 repo 根目錄執行）。
"""
import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from .errors import InputError
from .schemas import ApproachConfig, ExperimentConfig


@dataclass(frozen=True)
class PlannedApproach:
    """實驗裡的一個 Approach：設定、每篇文章每個 Direction 的 Run 次數，以及設定的 hash。"""
    config: ApproachConfig
    runs: int
    config_hash: str


@dataclass(frozen=True)
class Experiment:
    config: ExperimentConfig
    approaches: list[PlannedApproach]


def read_model(path: str, model: type[BaseModel]):
    """讀 JSON 檔並以 model 驗證。錯誤訊息只列欄位位置和原因，不回顯輸入值。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as e:
        raise InputError(f"{path}：讀不到檔案（{type(e).__name__}）") from e
    except json.JSONDecodeError as e:
        raise InputError(f"{path}：不是合法的 JSON（第 {e.lineno} 行）") from e
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        raise InputError([f"{path}：{'.'.join(str(p) for p in err['loc']) or '(根)'}：{err['msg']}" for err in e.errors()]) from e


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
    return Experiment(config=config, approaches=approaches)
