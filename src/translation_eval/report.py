"""report：由 Run 紀錄和 check 結果產生 Markdown 報告。每個 Approach × Direction × 輸入版本一節，列出 Block 率
（按 Run、按文章各一個數字）、Refusal 率、其他失敗、漏段數，以及 Deterministic Finding 和警示的數量；
有 main-only 的 Run 時，另有「Block 誘因實驗」一節比較兩個輸入版本。

用法：
  uv run --locked python -I -m translation_eval report --config <實驗設定> [--data-dir data]

每個比率都附分子和分母。還有 ok 的 Run 沒有 check 結果時不產生報告：否則漏段數會靜默地少算。
"""
from collections import Counter, defaultdict

from .check import current_check_meta
from .config import Experiment, PlannedApproach, length_ranges, load_experiment
from .articles import load_article
from .errors import InputError
from .judge_report import judge_lines, judge_settings_lines
from .schemas import ComponentResult, Dimension, Direction, InputVariant, Outcome, RunKey, RunRecord, WarningKind
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


def section_key(item) -> tuple[str, Direction, InputVariant]:
    """報告的一節：Approach × Direction × 輸入版本。輸入版本不同的 Run 各有一節，不混在一起算。"""
    return item.approach, item.direction, item.input_variant


def format_ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return f"{numerator}/{denominator}（—）"
    return f"{numerator}/{denominator}（{100 * numerator / denominator:.1f}%）"


def _counts(values, none_label: str = "未回報") -> str:
    return "、".join(f"{v if v is not None else none_label}（{n} 個 Run）" for v, n in sorted(Counter(values).items(), key=lambda kv: str(kv[0])))


def render(experiment: Experiment, out: OutputDir, records: list[RunRecord], rows: list[ComponentResult], extra_lines: list[str] = ()) -> str:
    cfg = experiment.config
    lines = [
        f"# 評核報告：{cfg.id}", "",
        "## 實驗設定", "",
        f"- 實驗 id：{cfg.id}",
        f"- 文章：{len(cfg.articles.ids)} 篇（{'、'.join(cfg.articles.ids)}）",
        f"- Direction：{'、'.join(cfg.directions)}",
        f"- 輸入版本：{'、'.join(sorted({r.input_variant for r in records}))}",
        f"- Term List：{term_list_text(experiment)}",
        f"- Run 紀錄裡的 Term List hash：{_counts((r.term_list_hash for r in records if not r.is_baseline), none_label='未使用')}",
        "- 長度比例範圍（警示用）：" + "；".join(f"{d} {r.min}–{r.max}" for d, r in length_ranges(cfg).items() if d in cfg.directions),
        "", "## Approach 設定", "",
    ]
    for planned in experiment.approaches:
        lines += approach_lines(planned, [r for r in records if r.approach == planned.config.id])
    lines += judge_settings_lines(experiment, out)
    lines += ["## Prompt", ""]
    prompts = Counter((r.prompt_id, r.prompt_hash) for r in records if r.prompt_id is not None)
    lines += [f"- {prompt_id}：{prompt_hash}（{n} 個 Run）" for (prompt_id, prompt_hash), n in sorted(prompts.items())]
    lines.append("")
    baseline_ids = sorted({b.id for b in cfg.baselines} | {r.approach for r in records if r.is_baseline})   # 已匯入的也列出，不看現在的設定
    if baseline_ids:
        lines += ["## Baseline", ""]
        lines += [f"- {i}：{_counts(r.baseline_label for r in records if r.approach == i) or '尚未匯入'}" for i in baseline_ids]
        lines.append("")

    planned_runs = {(p.id, d, v): len(cfg.articles.ids) * p.runs
                    for p in experiment.approaches for d in cfg.directions for v in p.input_variants}
    planned_runs.update({(b.id, b.direction, InputVariant.FULL): len(cfg.articles.ids) for b in cfg.baselines})   # baseline 每篇一個 Run
    records_by_section, rows_by_section = defaultdict(list), defaultdict(list)
    for r in records:
        records_by_section[section_key(r)].append(r)
    for r in rows:
        rows_by_section[section_key(r)].append(r)
    sections = list(planned_runs) + sorted(set(records_by_section) - set(planned_runs))   # 紀錄裡有、設定裡沒有的也列出
    for key in sections:
        lines += section_lines(*key, planned_runs.get(key), records_by_section[key], rows_by_section[key])
    lines += block_incentive_lines(records)
    lines += extra_lines
    return "\n".join(lines).rstrip("\n") + "\n"


def block_rates(records: list[RunRecord]) -> str:
    """Block 率的兩個數字：按 Run 計；按文章計（文章只要有任何一個 Run 被 Block 就算）。"""
    articles = {r.article_id for r in records}
    blocked_articles = {r.article_id for r in records if r.outcome is Outcome.BLOCK}
    blocked_runs = sum(1 for r in records if r.outcome is Outcome.BLOCK)
    return f"按 Run {format_ratio(blocked_runs, len(records))}；按文章 {format_ratio(len(blocked_articles), len(articles))}"


def _blocked_and_total(records: list[RunRecord], article_id: str) -> tuple[int, int]:
    mine = [r for r in records if r.article_id == article_id]
    return sum(1 for r in mine if r.outcome is Outcome.BLOCK), len(mine)


def block_incentive_lines(records: list[RunRecord]) -> list[str]:
    """Block 誘因實驗：周邊內容會不會誘發 Block。只比較兩個輸入版本都有可比較紀錄的文章。"""
    if not any(r.input_variant is InputVariant.MAIN_ONLY for r in records):
        return []
    full, main_only = InputVariant.FULL, InputVariant.MAIN_ONLY
    lines = ["## Block 誘因實驗（full 與 main-only）", "",
             "只比較兩個輸入版本都有紀錄的文章；只有一個版本的不在內。error 的 Run 不是內容造成的結果，不計入比較；"
             "某個版本只有 error 的文章也不比較（先用 --redo-errors 重做）。「結果不同」指文章在兩個輸入版本下被 Block 的比例不一樣，"
             "括號裡是各版本被 Block 的 Run 數／Run 數。", ""]
    for approach_id, direction in sorted({(r.approach, r.direction) for r in records if r.input_variant is main_only}):
        runs = {v: [r for r in records if section_key(r) == (approach_id, direction, v)] for v in (full, main_only)}
        recorded_in_both = {r.article_id for r in runs[full]} & {r.article_id for r in runs[main_only]}
        lines += [f"### {approach_id} × {direction}", ""]
        if not recorded_in_both:
            lines += ["沒有兩個輸入版本都有紀錄的文章。", ""]
            continue
        usable = {v: [r for r in rs if r.outcome is not Outcome.ERROR] for v, rs in runs.items()}
        common = recorded_in_both & {r.article_id for r in usable[full]} & {r.article_id for r in usable[main_only]}
        compared = {v: [r for r in rs if r.article_id in common] for v, rs in usable.items()}
        different = []
        for article_id in sorted(common):
            (full_blocked, full_total), (only_blocked, only_total) = (_blocked_and_total(compared[v], article_id) for v in (full, main_only))
            if full_blocked * only_total != only_blocked * full_total:   # 分數相等就是一樣，Run 次數不同也不影響
                different.append(f"{article_id}（full {full_blocked}/{full_total}、main-only {only_blocked}/{only_total}）")
        lines += [f"- 比較的文章：{len(common)} 篇",
                  f"- full：{block_rates(compared[full])}",
                  f"- main-only：{block_rates(compared[main_only])}",
                  f"- 結果不同的文章：{'、'.join(different) or '無'}"]
        if recorded_in_both - common:
            lines.append(f"- 因某個輸入版本只有 error 的 Run 而未比較：{len(recorded_in_both - common)} 篇（先用 --redo-errors 重做）")
        lines.append("")
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
        f"- 請求模式：{approach.request_mode}" + (f"（輸入上限 {approach.max_input_chars} 字，超過就按 Component 切塊）" if approach.max_input_chars else "") + f"；temperature：{temperature}；max_output_tokens：{max_tokens}；timeout：{approach.timeout_seconds} 秒；併發：{approach.concurrency}",
        f"- 重試：最多 {approach.retry.max_attempts} 次嘗試，退避起點 {approach.retry.base_delay_seconds} 秒",
        f"- 每篇每個 Direction 每個輸入版本的 Run 次數：{planned.runs}（輸入版本：{'、'.join(planned.input_variants)}）",
        f"- 設定 hash：{hash_text}",
        f"- 實際回應的模型版本：{_counts(r.model_version for r in records) or '無'}",
    ] + ([f"- 實際服務的 provider：{_counts((r.served_provider for r in records), none_label='回應沒有寫') or '無'}"]
         if approach.provider == "openrouter" else []) + [""]


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
    articles = {}
    if experiment.judges:   # 只有 Judge 的分數需要讀文章；沒有 Judge 時不讀，文章檔不在也能出報告
        articles = {r.article_id: load_article(experiment.config.articles.dir, r.article_id) for r in records if r.outcome is Outcome.OK}
    out.write_report(render(experiment, out, records, rows, judge_lines(experiment, out, records, rows, articles)))
    print(f"已寫入 {out.report_path}（{len(records)} 個 Run）")
    return 0
