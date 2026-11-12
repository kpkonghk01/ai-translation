"""讀入並驗證實驗設定與 Approach 設定。任何一項不合法都丟出 InputError，由指令在寫出任何東西之前以非 0 結束。

設定檔裡的路徑都相對於目前的工作目錄（指令一律在 repo 根目錄執行）。
"""
import hashlib
import json
from collections import Counter
from dataclasses import dataclass

from .errors import InputError
from .loading import read_model
from .schemas import ApproachConfig, Direction, Domain, ExperimentConfig, InputVariant, JudgeConfig, JudgeRole, LengthRatioRange
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

    def primary_judge_for(self, group: str):
        """Gate 的分組由哪個 Judge 決定：(Judge, 是否暫定)。設定沒有為這個分組指定時，用第一個角色是 primary 的 Judge，
        並標為暫定；沒有 primary 的 Judge 時是 (None, True)。"""
        chosen = self.config.judging.primary_judges.get(group) if self.config.judging else None
        if chosen is not None:
            return next(j for j in self.judges if j.id == chosen), False
        return next((j for j in self.judges if j.config.role is JudgeRole.PRIMARY), None), True

    def display_names(self) -> dict[str, str]:
        """代號 → 給人看的名稱（模型與 baseline）。沒有顯示名稱時，模型用 model、baseline 用它的 id（所以 baseline 要寫）。
        兩個名稱相同（沒有顯示名稱而 model 相同，或名稱剛好等於別人的 model）兩列會分不出來，所以停止。"""
        names = {p.id: p.config.display_name or p.config.model for p in self.approaches}
        names.update({b.id: b.display_name or b.id for b in self.config.baselines})
        if problems := display_name_problems(names.values()):
            raise InputError(problems)
        return names

    def approach_config(self, approach_id: str) -> ApproachConfig | None:
        """LLM Approach 的設定；baseline（不經 API）沒有。"""
        return next((p.config for p in self.approaches if p.id == approach_id), None)


OFF_BY_DEFAULT_SWITCHES = ("reasoning_off", "single_user_message")


def config_hash(approach: ApproachConfig) -> str:
    settings = approach.model_dump(mode="json")
    del settings["display_name"]   # 只是給人看的名稱，不影響請求
    for switch in OFF_BY_DEFAULT_SWITCHES:   # 後來才加的開關：沒開時不進 hash，已有 Run 的 hash 才不會變（否則報告會把它們都標成「與目前設定不同」）
        if not settings[switch]:
            del settings[switch]
    canonical = json.dumps(settings, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# 譯文與原文長度之比的預設範圍：暫定，等有繁→英的實際分佈再定；實驗設定的 length_ratio 可以按 Direction 覆寫
DEFAULT_LENGTH_RATIO = {
    Direction.TRAD_TO_EN: LengthRatioRange(min=1.5, max=8.0),
    Direction.TRAD_TO_SIMPLIFIED: LengthRatioRange(min=0.6, max=1.6),
}


def length_ranges(config: ExperimentConfig) -> dict[Direction, LengthRatioRange]:
    """實際使用的長度比例範圍：預設值加上實驗設定的覆寫。"""
    return {**DEFAULT_LENGTH_RATIO, **config.length_ratio}


def display_name_problems(names) -> list[str]:
    """重覆的顯示名稱（不分大小寫、連續空白視為一個：兩列看起來一樣就分不出來）。載入設定和展示報告共用同一套檢查。"""
    first, count = {}, Counter()
    for name in names:
        key = " ".join(name.split()).casefold()
        first.setdefault(key, name)
        count[key] += 1
    return [f"顯示名稱重覆：{first[key]}（在 Approach 設定改成不同的 display_name）" for key, n in count.items() if n > 1]


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
    named = [a.config.display_name for a in approaches if a.config.display_name] + [b.display_name for b in config.baselines if b.display_name]
    problems += display_name_problems(named)
    judges = load_judges(config, seen, problems)
    problems += gate_config_problems(config, judges)
    problems += comparison_problems(config, approaches)
    problems += crosscheck_problems(config, judges)
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


def gate_config_problems(config: ExperimentConfig, judges: list[PlannedJudge]) -> list[str]:
    """Gate 的設定：分組覆寫指向實驗內的文章；每個分組的主 Judge 是存在的、角色是 primary 的 Judge，分組名是已知的。"""
    problems = []
    if config.ground_truth is not None:
        problems += [f"ground_truth.group_overrides 指向不在實驗內的文章：{a}"
                     for a in config.ground_truth.group_overrides if a not in config.articles.ids]
    primary = config.judging.primary_judges if config.judging else {}
    if primary and config.ground_truth is None:
        problems.append("judging.primary_judges 要有 ground_truth：主 Judge 是為 Gate 指定的")
    groups = {d.value for d in Domain} | set(config.ground_truth.group_overrides.values() if config.ground_truth else ())
    by_id = {j.id: j.config.role for j in judges}
    for group, judge_id in primary.items():
        if group not in groups:
            problems.append(f"judging.primary_judges 的分組不存在：{group}（有：{'、'.join(sorted(groups))}）")
        if by_id.get(judge_id) is not JudgeRole.PRIMARY:
            problems.append(f"judging.primary_judges 的 Judge {judge_id}（分組 {group}）不存在，或角色不是 primary")
    return problems


def comparison_problems(config: ExperimentConfig, approaches: list[PlannedApproach]) -> list[str]:
    """比較用的 id 要是實驗內的 Approach 或 baseline，而且在要比的 Direction 有 full 的 Run：沒有的話比較會什麼也不列，
    悄悄漏掉。質素的 baseline 是用 API 跑的 Approach（方案 A），不是匯入的 baseline。"""
    comparison = config.comparison
    if comparison is None:
        return []
    by_id = {p.id: p for p in approaches}
    baselines = {b.id: b for b in config.baselines}

    def covers(who: str, direction) -> bool:
        if who in by_id:
            return InputVariant.FULL in by_id[who].input_variants
        return who in baselines and baselines[who].direction == direction

    problems = []
    if comparison.baseline not in by_id:
        problems.append(f"comparison.baseline 要是實驗內的 Approach（不是匯入的 baseline）：{comparison.baseline}")
    elif not covers(comparison.baseline, None):
        problems.append(f"comparison.baseline 沒有 full 的 Run：{comparison.baseline}")
    for direction, ids in comparison.also_against.items():
        if direction not in config.directions:
            problems.append(f"comparison.also_against 的 Direction 不在實驗內：{direction}")
            continue
        for who in ids:
            if who not in by_id and who not in baselines:
                problems.append(f"comparison.also_against.{direction} 指向不存在的 Approach 或 baseline：{who}")
            elif not covers(who, direction):
                problems.append(f"comparison.also_against.{direction} 的 {who} 在這個 Direction 沒有 full 的 Run")
    return problems


def crosscheck_problems(config: ExperimentConfig, judges: list[PlannedJudge]) -> list[str]:
    """交叉檢驗用的 id 要是 judging.judges 裡的 Judge；要有 ground truth 才有東西對照；盲抽的模型與對照的不是同一個。"""
    crosscheck = config.crosscheck
    if crosscheck is None:
        return []
    known = {j.id for j in judges}
    problems = []
    if config.ground_truth is None:
        problems.append("crosscheck 要有 ground_truth：交叉檢驗是拿模型選的句子與 ground truth 對照")
    for where, who in (("judge", crosscheck.judge), ("reference_judge", crosscheck.reference_judge)):
        if who is not None and who not in known:
            problems.append(f"crosscheck.{where} 不在 judging.judges 裡：{who}")
    if crosscheck.judge == crosscheck.reference_judge:
        problems.append("crosscheck.reference_judge 不可以與 judge 是同一個：對照要用另一個模型")
    return problems
