"""translate：把文章送去各個 Approach 翻譯，記錄每個 Run 的結果。

用法：
  uv run --locked python -I -m translation_eval translate --config <實驗設定> [--data-dir data]
      [--dry-run] [--article <id>]... [--limit <N>] [--redo-errors]

Run 的 key 是「文章 × Approach × Direction × 輸入版本 × Run 序號」。已經有紀錄的 Run 會略過，所以中斷後
重跑同一條指令即可續跑；error 的 Run 要加 --redo-errors 才會重做。
"""
import json
import os
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone

from pydantic import ValidationError

from .articles import load_article, request_payload
from .config import PlannedApproach, load_experiment
from .errors import InputError
from .exchange import execute
from .outcome import Interpretation
from .prompts import translation_template
from .providers import adapter_for
from .schemas import Article, InputVersion, Outcome, RunKey, RunRecord, Translation
from .store import OutputDir
from .transport import Transport


def plan(experiment, article_ids):
    """要執行的 Run：依文章、Approach、Direction、Run 序號的順序。"""
    for article_id in article_ids:
        for planned in experiment.approaches:
            for direction in experiment.config.directions:
                for run_index in range(1, planned.runs + 1):
                    key = RunKey(article_id=article_id, approach=planned.config.id, direction=direction,
                                 input_version=InputVersion.FULL, run_index=run_index)
                    yield key, planned


def select_articles(configured: list[str], requested: list[str]) -> list[str]:
    unknown = [a for a in requested if a not in configured]
    if unknown:
        raise InputError([f"--article 的文章不在實驗設定內：{a}" for a in unknown])
    return [a for a in configured if not requested or a in requested]


def user_text(article: Article) -> str:
    return json.dumps(request_payload(article), ensure_ascii=False)


def run(args, transport: Transport) -> int:
    experiment = load_experiment(args.config)
    cfg = experiment.config
    article_ids = select_articles(cfg.articles.ids, args.article)
    articles = {i: load_article(cfg.articles.dir, i) for i in article_ids}
    out = OutputDir(args.data_dir, cfg.id)
    done = {RunKey.of(r): r for r in out.read_runs()}

    pending, skipped = [], 0
    for key, planned in plan(experiment, article_ids):
        record = done.get(key)
        if record is None or (args.redo_errors and record.outcome is Outcome.ERROR):
            pending.append((key, planned))
        else:
            skipped += 1
    if args.limit is not None:
        pending = pending[: args.limit]

    if args.dry_run:
        return dry_run(pending, skipped, articles)

    # 缺少 key 時在送出任何請求之前就停止，只列出變數名稱
    missing = sorted({p.config.api_key_env for _, p in pending if not os.environ.get(p.config.api_key_env)})
    if missing:
        raise InputError([f"缺少環境變數 {name}" for name in missing])

    redo = {key for key, _ in pending if key in done}
    if redo:
        out.drop_runs(redo)
    outcomes = Counter()
    for key, planned in pending:
        record = translate_one(transport, key, planned, articles[key.article_id], out)
        outcomes[record.outcome] += 1
        print(f"{key.article_id} {key.approach} {key.direction} {key.input_version} r{key.run_index}: {record.outcome}")
    summary = "、".join(f"{o} {n}" for o, n in sorted(outcomes.items())) or "沒有要執行的 Run"
    print(f"完成 {len(pending)} 個 Run（已有紀錄而略過：{skipped}）：{summary}")
    return 0


def dry_run(pending, skipped: int, articles) -> int:
    chars = 0
    endpoints = {}
    for key, planned in pending:
        chars += len(translation_template(key.direction).text) + len(user_text(articles[key.article_id]))
        endpoints[planned.config.id] = adapter_for(planned.config).endpoint(planned.config)
    print("dry-run：不送出任何請求，也不寫出任何檔案")
    print(f"Run：{len(pending)}（已有紀錄而略過：{skipped}）")
    print(f"請求數：{len(pending)}")
    print(f"估計輸入字數：{chars}")
    for approach_id, endpoint in endpoints.items():
        print(f"endpoint：{approach_id} {endpoint}")
    return 0


def require_translation_shape(result: Interpretation) -> Interpretation:
    """ok 的 JSON 還要有譯文的結構（components 是物件清單、每個有 id 與 type），否則是 malformed。
    Component 缺少、多出或 type 被改不在這裡判：那是 check 的工作，Run 本身仍是 ok。"""
    if result.outcome is not Outcome.OK:
        return result
    try:
        Translation.model_validate(result.payload)
    except ValidationError:
        return replace(result, outcome=Outcome.MALFORMED, reason="JSON 不是譯文的結構", payload=None)
    return result


def translate_one(transport: Transport, key: RunKey, planned: PlannedApproach, article: Article, out: OutputDir) -> RunRecord:
    approach = planned.config
    adapter = adapter_for(approach)
    template = translation_template(key.direction)
    api_key = os.environ[approach.api_key_env]
    request = adapter.build_request(approach, template.text, user_text(article), api_key)

    exchange = execute(transport, adapter, request, approach.retry, secrets=(api_key,))
    result = require_translation_shape(exchange.interpretation)
    record = RunRecord(
        **key.model_dump(),
        outcome=result.outcome,
        reason=result.reason,
        attempts=exchange.attempts,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        thought_tokens=result.thought_tokens,
        latency_seconds=exchange.attempts[-1].latency_seconds,
        concurrency=approach.concurrency,
        provider=approach.provider,
        model_version=result.model_version,
        prompt_id=template.id,
        prompt_hash=template.hash,
        config_hash=planned.config_hash,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    translation = result.payload if result.outcome is Outcome.OK else None
    return out.write_run(record, exchange.raw_body, translation)
