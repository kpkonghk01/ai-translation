"""translate：把文章送去各個 Approach 翻譯，記錄每個 Run 的結果。

用法：
  uv run --locked python -I -m translation_eval translate --config <實驗設定> [--data-dir data]
      [--dry-run] [--article <id>]... [--limit <N>] [--redo-errors]

Run 的 key 是「文章 × Approach × Direction × 輸入版本 × Run 序號」。已經有紀錄的 Run 會略過，所以中斷後
重跑同一條指令即可續跑；error 的 Run 要加 --redo-errors 才會重做。

一個 Run 可能要送出好幾個請求（切塊、逐 Component，見 units.py）：結果取最差的一個（block > refusal > malformed >
truncated > error > ok），並記下是哪一個；遇到 block 就不再送後面的請求，因為結果已經是最差的了。
"""
import json
import os
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone

from pydantic import ValidationError

from .articles import apply_variant, field_problems, load_article
from .config import PlannedApproach, load_experiment
from .errors import InputError
from .exchange import Exchange, send_with_retries
from .outcome import Interpretation, classify_plain_text, classify_text, refusal_or_malformed
from .prompts import PromptTemplate, translation_template
from .providers import adapter_for
from .schemas import Article, Direction, Outcome, RunKey, RunRecord, Translation
from .store import OutputDir
from .terms import LoadedTermList, entries_in
from .transport import Transport
from .units import Unit, merge_chunks, merge_components, plan_units

# 結果由差到不差：一個 Run 的結果取最差的請求
SEVERITY = {Outcome.BLOCK: 5, Outcome.REFUSAL: 4, Outcome.MALFORMED: 3, Outcome.TRUNCATED: 2, Outcome.ERROR: 1, Outcome.OK: 0}

def plan(experiment, article_ids):
    """要執行的 Run。文章放在最外層：用 --limit 試跑時，會先把第一篇的所有 Run 跑完，而不是每篇各跑一個。"""
    for article_id in article_ids:
        for planned in experiment.approaches:
            for direction in experiment.config.directions:
                for variant in planned.input_variants:
                    for run_index in range(1, planned.runs + 1):
                        key = RunKey(article_id=article_id, approach=planned.id, direction=direction,
                                     input_variant=variant, run_index=run_index)
                        yield key, planned


def select_articles(configured: list[str], requested: list[str]) -> list[str]:
    unknown = [a for a in requested if a not in configured]
    if unknown:
        raise InputError([f"--article 的文章不在實驗設定內：{a}" for a in unknown])
    return [a for a in configured if not requested or a in requested]


def unit_prompt(template: PromptTemplate, unit: Unit, term_list: LoadedTermList, direction: Direction) -> str:
    """範本加上這個請求的原文出現過的 Term List 條目：沒出現的條目不注入。"""
    return template.render(entries_in(term_list.entries, unit.source_text, direction), direction)


def handle(args, transport: Transport) -> int:
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
        variants = sorted({v for p in experiment.approaches for v in p.input_variants})
        return dry_run(pending, skipped, articles, experiment.term_list, variants)

    # 缺少 key 時在送出任何請求之前就停止，只列出變數名稱
    missing = sorted({p.config.api_key_env for _, p in pending if not os.environ.get(p.config.api_key_env)})
    if missing:
        raise InputError([f"缺少環境變數 {name}" for name in missing])
    # 送出任何請求之前就能發現的設定問題（例如 OpenRouter 的允許清單還沒確認），整批一起停止
    not_ready = sorted({problem for _, p in pending for problem in adapter_for(p.config).problems(p.config)})
    if not_ready:
        raise InputError(not_ready)

    outcomes = Counter()
    for key, planned in pending:
        record = translate_one(transport, key, planned, articles[key.article_id], out, experiment.term_list,
                               replaces_existing=key in done)
        outcomes[record.outcome] += 1
        print(f"{key.label}: {record.outcome}")
    summary = "、".join(f"{o} {n}" for o, n in sorted(outcomes.items())) or "沒有要執行的 Run"
    print(f"完成 {len(pending)} 個 Run（已有紀錄而略過：{skipped}）：{summary}")
    return 0


def dry_run(pending, skipped: int, articles, term_list: LoadedTermList, variants) -> int:
    chars_by_variant, requests_by_variant = Counter(), Counter()
    destinations = {}
    for key, planned in pending:
        sent = apply_variant(articles[key.article_id], key.input_variant)
        template = translation_template(key.direction, planned.config.request_mode)
        for unit in plan_units(sent, planned.config):
            chars_by_variant[key.input_variant] += len(unit_prompt(template, unit, term_list, key.direction)) + len(unit.user_text)
            requests_by_variant[key.input_variant] += 1
        adapter = adapter_for(planned.config)
        destinations[planned.id] = (adapter.endpoint(planned.config), planned.config.concurrency, adapter.describe(planned.config))
    print("dry-run：不送出任何請求，也不寫出任何檔案")
    print(f"Run：{len(pending)}（已有紀錄而略過：{skipped}）")
    print(f"請求數：{sum(requests_by_variant.values())}")
    for variant in variants:   # 沒有要送的版本也列出，免得以為漏了
        print(f"輸入版本 {variant}：請求數 {requests_by_variant[variant]}、估計輸入字數 {chars_by_variant[variant]}")
    print(f"估計輸入字數：{sum(chars_by_variant.values())}")
    for approach_id, (endpoint, concurrency, extra_lines) in destinations.items():
        print(f"endpoint：{approach_id} {endpoint}（併發：{concurrency}）")
        for line in extra_lines:
            print(f"{line}")
    return 0


def require_translation_shape(result: Interpretation) -> Interpretation:
    """ok 的 JSON 還要有譯文的結構（components 是物件清單、每個有 id 與 type，文字欄位的型別對），否則不是預期的 JSON：
    符合拒絕語的樣式是 refusal，否則是 malformed。
    Component 缺少、多出或 type 被改不在這裡判：那是 check 的工作，Run 本身仍是 ok。"""
    if result.outcome is not Outcome.OK:
        return result
    try:
        translation = Translation.model_validate(result.payload)
    except ValidationError:
        outcome, reason, _ = refusal_or_malformed(json.dumps(result.payload, ensure_ascii=False), "JSON 不是譯文的結構")
        return replace(result, outcome=outcome, reason=reason, payload=None)
    if any(field_problems(c.type, c.model_dump()) for c in translation.components):
        return replace(result, outcome=Outcome.MALFORMED, reason="譯文的文字欄位型別不符", payload=None)
    return result


def _sum(values) -> int | None:
    numbers = [v for v in values if v is not None]
    return sum(numbers) if numbers else None


def _raw_lines(sent_units: list[tuple[Unit, Exchange]]) -> bytes:
    """好幾個請求的原始回應：每行一個請求，記下它是哪一個、HTTP 狀態和 response body 的文字。"""
    lines = [json.dumps({"unit": unit.label, "http_status": exchange.attempts[-1].http_status,
                         "body": None if exchange.raw_body is None else exchange.raw_body.decode("utf-8", errors="replace")},
                        ensure_ascii=False) for unit, exchange in sent_units]
    return ("\n".join(lines) + "\n").encode("utf-8")


def translate_one(transport: Transport, key: RunKey, planned: PlannedApproach, article: Article, out: OutputDir,
                  term_list: LoadedTermList, *, replaces_existing: bool) -> RunRecord:
    approach = planned.config
    adapter = adapter_for(approach)
    json_mode = approach.request_mode == "article-json"
    template = translation_template(key.direction, approach.request_mode)
    api_key = os.environ[approach.api_key_env]
    sent = apply_variant(article, key.input_variant)
    units = plan_units(sent, approach)

    results: list[tuple[Unit, Exchange, Interpretation]] = []
    for unit in units:
        request = adapter.build_request(approach, unit_prompt(template, unit, term_list, key.direction), unit.user_text, api_key)
        exchange = send_with_retries(transport, adapter, request, approach, classify_text if json_mode else classify_plain_text)
        result = require_translation_shape(exchange.interpretation) if json_mode else exchange.interpretation
        results.append((unit, exchange, result))
        if result.outcome is Outcome.BLOCK:   # 結果已經是最差的：不再送後面的請求
            break
    worst_unit, _, worst = max(results, key=lambda item: SEVERITY[item[2].outcome])   # 一樣差的，取最前面的那個
    several = len(units) > 1

    translation = None
    if worst.outcome is Outcome.OK:
        payloads = [result.payload for _, _, result in results]
        translation = (merge_components(sent, units, payloads) if not json_mode
                       else merge_chunks(payloads) if several else payloads[0])
    served = list(dict.fromkeys(r.served_provider for _, _, r in results if r.served_provider))
    record = RunRecord(
        **key.model_dump(),
        outcome=worst.outcome,
        reason=worst.reason,
        attempts=[a.model_copy(update={"unit": unit.label}) if several else a for unit, ex, _ in results for a in ex.attempts],
        input_tokens=_sum(r.input_tokens for _, _, r in results),
        output_tokens=_sum(r.output_tokens for _, _, r in results),
        thought_tokens=_sum(r.thought_tokens for _, _, r in results),
        latency_seconds=sum(ex.attempts[-1].latency_seconds for _, ex, _ in results),
        concurrency=approach.concurrency,
        provider=approach.provider,
        model_version=next((r.model_version for _, _, r in results if r.model_version), None),
        served_provider=", ".join(served) or None,
        worst_unit=worst_unit.label if several and worst.outcome is not Outcome.OK else None,
        prompt_id=template.id,
        prompt_hash=template.hash,
        term_list_hash=term_list.prompt_hash,
        config_hash=planned.config_hash,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    raw = _raw_lines([(unit, ex) for unit, ex, _ in results]) if several else results[0][1].raw_body
    return out.write_run(record, raw, translation, secrets=(api_key,), replaces_existing=replaces_existing,
                         raw_extension="jsonl" if several else "body")
