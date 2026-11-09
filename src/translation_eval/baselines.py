"""import-baseline：把方案 E（Current service）已重現的 baseline 匯入成 Run。

用法：
  uv run --locked python -I -m translation_eval import-baseline --config <實驗設定> [--data-dir data]

實驗設定的 baselines 列出每份 baseline 的 id、目錄和 Direction；目錄裡每篇文章一個 JSON，結構與譯文相同
（tools/service_baseline.py 的輸出），meta.baseline 是標籤（服務與 opencc 的版本）。沒有標籤的 baseline 會被拒絕：
沒有版本，報告就沒辦法寫明比較的是哪一版。匯入的 Run 結果是 ok，沒有 tokens 和 latency；之後與其他 Approach 一樣經過 check。
全部驗證通過才寫出，所以失敗時不會留下一半的 Run。已有紀錄的 Run 會略過。
"""
import os
from datetime import datetime, timezone

from .articles import field_problems
from .config import load_experiment
from .errors import InputError
from .loading import read_model
from .schemas import InputVariant, Outcome, RunKey, RunRecord, Translation
from .store import OutputDir

TRANSLATION_KEYS = ("title", "tags", "components")


def _label_of(baseline: Translation) -> str | None:
    meta = (baseline.model_extra or {}).get("meta")
    label = meta.get("baseline") if isinstance(meta, dict) else None
    return label.strip() if isinstance(label, str) and label.strip() else None


def handle(args) -> int:
    experiment = load_experiment(args.config)
    cfg = experiment.config
    if not cfg.baselines:
        raise InputError("實驗設定沒有 baselines，沒有東西可以匯入")
    out = OutputDir(args.data_dir, cfg.id)
    existing = {RunKey.of(r): r for r in out.read_runs()}

    problems, to_import, skipped = [], [], 0
    for baseline in cfg.baselines:
        loaded, labels = {}, {}
        for article_id in cfg.articles.ids:
            try:
                parsed = read_model(os.path.join(baseline.dir, f"{article_id}.json"), Translation)
            except InputError as e:
                problems += e.problems
                continue
            bad_fields = [c.id for c in parsed.components if field_problems(c.type, c.model_dump())]
            label = _label_of(parsed)
            if bad_fields:
                problems.append(f"baseline {baseline.id}：文章 {article_id}：Component 的文字欄位型別不對：{'、'.join(bad_fields)}")
            elif label is None:
                problems.append(f"baseline {baseline.id}：文章 {article_id}：沒有 meta.baseline 標籤")
            else:
                loaded[article_id], labels[article_id] = parsed, label
        if len(set(labels.values())) > 1:
            problems.append(f"baseline {baseline.id}：同一份 baseline 有 {len(set(labels.values()))} 種不同的 meta.baseline 標籤")
        for article_id, parsed in loaded.items():
            key = RunKey(article_id=article_id, approach=baseline.id, direction=baseline.direction,
                         input_variant=InputVariant.FULL, run_index=1)
            previous = existing.get(key)
            if previous is None:
                to_import.append((key, labels[article_id], parsed))
            elif previous.baseline_label != labels[article_id]:
                problems.append(f"baseline {baseline.id}：文章 {article_id}：已匯入的 Run 的標籤與目錄裡的不同，請用新的實驗 id")
            else:
                skipped += 1
    if problems:
        raise InputError(problems)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key, label, parsed in to_import:
        data = parsed.model_dump(mode="json")
        translation = {k: data[k] for k in TRANSLATION_KEYS if data.get(k) is not None}
        record = RunRecord(**key.model_dump(), outcome=Outcome.OK, attempts=[], baseline_label=label, created_at=now)
        out.write_run(record, None, translation)
    print(f"匯入 {len(to_import)} 個 Run（已有紀錄而略過：{skipped}）")
    return 0
