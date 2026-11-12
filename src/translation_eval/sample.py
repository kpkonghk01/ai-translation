"""抽樣實驗：從一個實驗抽出幾篇文章，每個模型只取第 1 次、只取完整文章，把現有的 Run 紀錄與譯文原樣複製成一個新的實驗。
用來縮小 Judge 要評的範圍：Judge 的費用與時間跟 Run 數成正比，全部評太貴太慢。

用法：
  uv run --locked python -I -m translation_eval sample --config <實驗設定> --id <新實驗 id> [--per-group 10] [--controls 3] [--seed 1] [--data-dir data]

每組（ground truth 的題材，或 group_overrides 的分組）抽 --per-group 篇，對照文章另外抽 --controls 篇；組裡的文章比要抽的少就全取。
抽的依據只有種子與 ground truth（種子加文章 id 的 hash 排序），不看 Run 的結果：不能看結果挑。沒有 ground truth 的文章不抽。
寫出新實驗的輸出目錄（runs.jsonl、translations/、raw/、sample.json）和新實驗的設定檔（與來源設定檔同一個目錄、檔名是 <新實驗 id>.json）：
設定與來源一樣，只是文章換成抽中的、每個 Approach 只有 1 次與完整文章。baseline 的 Run 也一起帶過去。
新實驗或設定檔已經存在、缺少要複製的檔案時，什麼都不寫。來源的 Run 還沒跑完也可以抽：沒複製的 Run，新實驗的 translate 會補跑。
"""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .config import load_experiment
from .errors import InputError
from .ground_truth import load_ground_truth
from .loading import read_model
from .articles import load_article
from .schemas import ExperimentConfig, GtFile, InputVariant, Slug
from .showcase import group_label, ordered_groups
from .store import OutputDir

CONTROLS = "對照"


def rank(seed: int, article_id: str) -> str:
    return hashlib.sha256(f"{seed}:{article_id}".encode("utf-8")).hexdigest()


def choose(article_ids: list[str], count: int, seed: int) -> set[str]:
    return set(sorted(article_ids, key=lambda i: rank(seed, i))[:count])


def check_options(args) -> None:
    problems = []
    try:
        TypeAdapter(Slug).validate_python(args.id)
    except ValidationError:
        problems.append("--id 要是由字母、數字、_、- 組成的名稱（開頭是字母或數字，64 字以內）")
    if args.per_group < 1:
        problems.append("--per-group 必須是 1 或以上")
    if args.controls < 0:
        problems.append("--controls 不可以是負數")
    if problems:
        raise InputError(problems)


def new_config(source: dict, new_id: str, chosen_in_order: list[str]) -> dict:
    """與來源設定一樣，只是文章換成抽中的、每個 Approach 只有 1 次與完整文章。group_overrides 只留下抽中的文章（指向不在實驗內的文章會被拒絕）。"""
    config = {**source, "id": new_id, "articles": {**source["articles"], "ids": chosen_in_order},
              "approaches": [{**entry, "runs": 1, "input_variants": [InputVariant.FULL.value]} for entry in source["approaches"]]}
    overrides = config.get("ground_truth", {}).get("group_overrides")
    if overrides:
        config["ground_truth"] = {**config["ground_truth"], "group_overrides": {a: g for a, g in overrides.items() if a in chosen_in_order}}
    return config


def handle(args) -> int:
    check_options(args)
    experiment = load_experiment(args.config)
    cfg = experiment.config
    if cfg.ground_truth is None:
        raise InputError("抽樣要有 ground truth：「每組抽幾篇」的組來自 ground truth")
    source, target = OutputDir(args.data_dir, cfg.id), OutputDir(args.data_dir, args.id)
    config_path = Path(args.config).parent / f"{args.id}.json"
    exists = [str(p) for p in (target.root, config_path) if p.exists()]
    if exists:
        raise InputError([f"已經存在：{p}（不覆蓋）" for p in exists])
    records = source.require_runs()

    articles = {i: load_article(cfg.articles.dir, i) for i in cfg.articles.ids}
    truth = load_ground_truth(cfg.ground_truth, articles)
    roles = {a.id: a.role for path in cfg.ground_truth.paths for a in read_model(path, GtFile).articles}
    groups, controls = defaultdict(list), []
    for article_id in cfg.articles.ids:   # 沒有 ground truth 的文章不抽
        if article_id in truth.domains:
            (controls if roles.get(article_id) == "control" else groups[truth.group_of(article_id)]).append(article_id)
    chosen = set(choose(controls, args.controls, args.seed)) if args.controls else set()
    chosen_by_group = {group: choose(ids, args.per_group, args.seed) for group, ids in groups.items()}
    chosen.update(*chosen_by_group.values())
    if not chosen:
        raise InputError("沒有抽到任何文章")

    known = {p.id for p in experiment.approaches} | {b.id for b in cfg.baselines}
    picked = [r for r in records if r.article_id in chosen and r.approach in known and r.input_variant is InputVariant.FULL and r.run_index == 1]
    if not picked:
        raise InputError("抽到的文章還沒有 Run 紀錄")
    ordered = [i for i in cfg.articles.ids if i in chosen]
    with open(args.config, encoding="utf-8") as f:
        config = new_config(json.load(f), args.id, ordered)
    try:
        ExperimentConfig.model_validate(config)
    except ValidationError as e:
        raise InputError(f"新實驗的設定不合法（{e.error_count()} 個錯誤）") from e

    target.copy_runs(source, picked)   # 缺少檔案就在這裡停止，什麼都還沒寫
    target.write_sample_note({"from": cfg.id, "seed": args.seed, "per_group": args.per_group, "controls": args.controls, "articles": ordered})
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    shown = [f"{group_label(g)} {len(chosen_by_group[g])} 篇" for g in ordered_groups(chosen_by_group)]
    if args.controls:
        shown.append(f"{CONTROLS} {len(chosen & set(controls))} 篇")
    print(f"已建立實驗 {args.id}：{len(ordered)} 篇（{'、'.join(shown)}），{len(picked)} 個 Run，設定檔 {config_path}")
    return 0
