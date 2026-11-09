"""report：由 Run 紀錄和 check 結果產生 Markdown 報告，列出每個 Approach × Direction 的 Block 率、Refusal 率、
其他失敗和漏段數。

用法：
  uv run --locked python -I -m translation_eval report --config <實驗設定> [--data-dir data]

每個比率都附分子和分母。還有 ok 的 Run 沒有 check 結果時不產生報告：否則漏段數會靜默地少算。
"""
from collections import Counter

from .check import current_check_meta
from .config import Experiment, PlannedApproach, length_ranges, load_experiment
from .errors import InputError
from .schemas import ComponentResult, Dimension, InputVariant, Outcome, RunKey, RunRecord, WarningKind
from .store import OutputDir

MAX_LISTED = 20
CHECK_META_LABELS = {"rules_version": "規則版本", "term_list_hash": "Term List", "length_ratio": "長度比例範圍"}
WARNING_LABELS = {
    WarningKind.LENGTH_RATIO: "長度比例",
    WarningKind.NUMBER_MISSING: "數字遺失",
    WarningKind.QUOTE_COUNT: "引語數量",
    WarningKind.SUSPECTED_REFUSAL: "疑似 Refusal",
    WarningKind.UNCHANGED_TRANSLATABLE: "譯文與原文相同",
    WarningKind.UNTRANSLATABLE_CHANGED: "不翻譯的 Component 被改動",
}


def format_ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return f"{numerator}/{denominator}（—）"
    return f"{numerator}/{denominator}（{100 * numerator / denominator:.1f}%）"


def _counts(values, none_label: str = "未回報") -> str:
    return "、".join(f"{v if v is not None else none_label}（{n} 個 Run）" for v, n in sorted(Counter(values).items(), key=lambda kv: str(kv[0])))


def render(experiment: Experiment, records: list[RunRecord], rows: list[ComponentResult]) -> str:
    cfg = experiment.config
    lines = [
        f"# 評核報告：{cfg.id}", "",
        "## 實驗設定", "",
        f"- 實驗 id：{cfg.id}",
        f"- 文章：{len(cfg.articles.ids)} 篇（{'、'.join(cfg.articles.ids)}）",
        f"- Direction：{'、'.join(cfg.directions)}",
        f"- 輸入版本：{'、'.join(sorted({r.input_variant for r in records}))}",
        f"- Term List：{term_list_text(experiment)}",
        f"- Run 紀錄裡的 Term List hash：{_counts((r.term_list_hash for r in records), none_label='未使用')}",
        "- 長度比例範圍（警示用）：" + "；".join(f"{d} {r.min}–{r.max}" for d, r in length_ranges(cfg).items() if d in cfg.directions),
        "", "## Approach 設定", "",
    ]
    for planned in experiment.approaches:
        lines += approach_lines(planned, [r for r in records if r.approach == planned.config.id])
    lines += ["## Prompt", ""]
    prompts = Counter((r.prompt_id, r.prompt_hash) for r in records)
    lines += [f"- {prompt_id}：{prompt_hash}（{n} 個 Run）" for (prompt_id, prompt_hash), n in sorted(prompts.items())]
    lines.append("")

    # 輸入版本不同的 Run 各有一節，不混在一起算
    planned_runs = {(p.id, d, v): len(cfg.articles.ids) * p.runs
                    for p in experiment.approaches for d in cfg.directions for v in p.input_variants}
    recorded = {(r.approach, r.direction, r.input_variant) for r in records}
    sections = list(planned_runs) + sorted(recorded - set(planned_runs))   # 紀錄裡有、設定裡沒有的也列出
    for approach_id, direction, variant in sections:
        def in_section(item):
            return (item.approach, item.direction, item.input_variant) == (approach_id, direction, variant)
        lines += section_lines(approach_id, direction, variant, planned_runs.get((approach_id, direction, variant)),
                               [r for r in records if in_section(r)], [r for r in rows if in_section(r)])
    lines += block_incentive_lines(records)
    return "\n".join(lines).rstrip("\n") + "\n"


def block_rates(records: list[RunRecord]) -> str:
    """Block 率的兩個數字：按 Run 計；按文章計（文章只要有任何一個 Run 被 Block 就算）。"""
    articles = {r.article_id for r in records}
    blocked_articles = {r.article_id for r in records if r.outcome is Outcome.BLOCK}
    blocked_runs = sum(1 for r in records if r.outcome is Outcome.BLOCK)
    return f"按 Run {format_ratio(blocked_runs, len(records))}；按文章 {format_ratio(len(blocked_articles), len(articles))}"


def block_incentive_lines(records: list[RunRecord]) -> list[str]:
    """Block 誘因實驗：周邊內容會不會誘發 Block。只比較兩個輸入版本都有紀錄的文章。"""
    if not any(r.input_variant is InputVariant.MAIN_ONLY for r in records):
        return []
    lines = ["## Block 誘因實驗（full 與 main-only）", "",
             "只比較兩個輸入版本都有紀錄的文章；只有一個版本的不在內。「結果不同」指文章在兩個輸入版本下，有沒有任何 Run 被 Block 不一樣。", ""]
    for approach_id, direction in sorted({(r.approach, r.direction) for r in records if r.input_variant is InputVariant.MAIN_ONLY}):
        by_variant = {v: [r for r in records if (r.approach, r.direction, r.input_variant) == (approach_id, direction, v)]
                      for v in (InputVariant.FULL, InputVariant.MAIN_ONLY)}
        common = {r.article_id for r in by_variant[InputVariant.FULL]} & {r.article_id for r in by_variant[InputVariant.MAIN_ONLY]}
        lines += [f"### {approach_id} × {direction}", ""]
        if not common:
            lines += ["沒有兩個輸入版本都有紀錄的文章。", ""]
            continue
        compared = {v: [r for r in rs if r.article_id in common] for v, rs in by_variant.items()}
        blocked = {v: {r.article_id for r in rs if r.outcome is Outcome.BLOCK} for v, rs in compared.items()}
        different = sorted(blocked[InputVariant.FULL] ^ blocked[InputVariant.MAIN_ONLY])
        lines += [f"- 比較的文章：{len(common)} 篇",
                  f"- full：{block_rates(compared[InputVariant.FULL])}",
                  f"- main-only：{block_rates(compared[InputVariant.MAIN_ONLY])}",
                  f"- 結果不同的文章：{'、'.join(different) or '無'}", ""]
    return lines


def term_list_text(experiment: Experiment) -> str:
    term_list = experiment.term_list
    if not term_list.is_used:
        return "未使用"
    return f"目前 {term_list.prompt_hash}（{len(term_list.entries)} 條）"


def approach_lines(planned: PlannedApproach, records: list[RunRecord]) -> list[str]:
    approach = planned.config
    hashes = Counter(r.config_hash for r in records)
    hash_text = "、".join(
        f"{h}（{n} 個 Run{'' if h == planned.config_hash else '，與目前設定不同'}）" for h, n in sorted(hashes.items())
    ) or f"{planned.config_hash}（目前設定，還沒有 Run）"
    temperature = "API 預設" if approach.temperature is None else approach.temperature
    max_tokens = "API 預設" if approach.max_output_tokens is None else approach.max_output_tokens
    return [
        f"### {approach.id}", "",
        f"- 模型家族：{approach.family}；provider：{approach.provider}；模型：{approach.model}",
        f"- 請求模式：{approach.request_mode}；temperature：{temperature}；max_output_tokens：{max_tokens}；timeout：{approach.timeout_seconds} 秒；併發：{approach.concurrency}",
        f"- 重試：最多 {approach.retry.max_attempts} 次嘗試，退避起點 {approach.retry.base_delay_seconds} 秒",
        f"- 每篇每個 Direction 的 Run 次數：{planned.runs}",
        f"- 設定 hash：{hash_text}",
        f"- 實際回應的模型版本：{_counts(r.model_version for r in records) or '無'}",
        "",
    ]


def section_lines(approach_id: str, direction: str, variant: InputVariant, planned: int | None,
                  records: list[RunRecord], rows: list[ComponentResult]) -> list[str]:
    n = len(records)
    by_outcome = Counter(r.outcome for r in records)
    reasons = Counter(r.reason for r in records if r.outcome is Outcome.BLOCK)
    reason_text = "、".join(f"{reason} {count}" for reason, count in sorted(reasons.items(), key=lambda kv: str(kv[0]))) or "無"
    if planned is None:
        recorded = f"不在目前的實驗設定內，已有紀錄 {n}"
    else:
        recorded = f"計畫 {planned}，已有紀錄 {n}" + ("（未完成）" if n < planned else "")
    omissions = sum(r.n_omissions for r in rows)
    checked_runs = len({RunKey.of(r) for r in rows})
    n_components = sum(1 for r in rows if r.source_type is not None)
    finding_counts = Counter(f.dimension for r in rows for f in r.findings)
    warning_counts = Counter(w.kind for r in rows for w in r.warnings)
    findings = "、".join(f"{d.value} {finding_counts[d]}" for d in Dimension)
    warnings = "、".join(f"{WARNING_LABELS[k]} {warning_counts[k]}" for k in WarningKind)
    others = "、".join(f"{o.value} {format_ratio(by_outcome[o], n)}" for o in (Outcome.MALFORMED, Outcome.TRUNCATED, Outcome.ERROR))
    return [
        f"## {approach_id} × {direction}" + ("" if variant is InputVariant.FULL else f"（{variant}）"), "",
        f"- Run：{recorded}",
        f"- Block 率：{block_rates(records)}；原因分佈：{reason_text}",
        f"- Refusal 率：{format_ratio(by_outcome[Outcome.REFUSAL], n)}",
        f"- 其他失敗：{others}",
        f"- 漏段數：{omissions}（已檢查 {checked_runs} 個 ok 的 Run、共 {n_components} 個 Component）",
        f"- Deterministic Finding：{findings}（已檢查 {checked_runs} 個 ok 的 Run、共 {n_components} 個 Component）",
        f"- 警示：{warnings}",
        "",
    ]


def require_fresh_check(experiment: Experiment, out: OutputDir, records: list[RunRecord]) -> None:
    """check 的結果取決於規則、Term List 和長度比例範圍。它們變了而沒有重跑 check，報告就會用舊結果，
    而且看不出來，所以不產生報告。沒有任何 ok 的 Run（例如全部被 Block）就沒有東西要檢查，不需要 check。"""
    if not any(r.outcome is Outcome.OK for r in records):
        return
    meta, current = out.read_check_meta(), current_check_meta(experiment)
    if meta == current:
        return
    if meta is None:
        reasons = ["沒有 check 紀錄，可能是舊版 check 跑的"]
    else:
        reasons = [CHECK_META_LABELS.get(name, name) for name in meta.differing_fields(current)]
    raise InputError(f"check 的結果已過期（{'、'.join(reasons)}），請重新執行 check")


def handle(args) -> int:
    experiment = load_experiment(args.config)
    out = OutputDir(args.data_dir, experiment.config.id)
    records = out.require_runs()
    rows = out.read_components()
    checked = {RunKey.of(r) for r in rows}
    unchecked = [r for r in records if r.outcome is Outcome.OK and RunKey.of(r) not in checked]
    if unchecked:
        problems = [f"ok 的 Run 還沒有 check 結果，先執行 check：{r.label}"
                    for r in unchecked[:MAX_LISTED]]
        if len(unchecked) > MAX_LISTED:
            problems.append(f"…另有 {len(unchecked) - MAX_LISTED} 個")
        raise InputError(problems)
    require_fresh_check(experiment, out, records)   # 先列出沒檢查過的 Run（較具體）；都檢查過了，才看結果是不是過期
    out.write_report(render(experiment, records, rows))
    print(f"已寫入 {out.report_path}（{len(records)} 個 Run）")
    return 0
