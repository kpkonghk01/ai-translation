"""report：由 Run 紀錄和 check 結果產生 Markdown 報告，列出每個 Approach × Direction 的 Block 率、Refusal 率、
其他失敗和漏段數。

用法：
  uv run --locked python -I -m translation_eval report --config <實驗設定> [--data-dir data]

每個比率都附分子和分母。還有 ok 的 Run 沒有 check 結果時不產生報告：否則漏段數會靜默地少算。
"""
from collections import Counter

from .check import OMISSION
from .config import Experiment, PlannedApproach, load_experiment
from .errors import InputError
from .schemas import ComponentResult, Outcome, RunKey, RunRecord
from .store import OutputDir

MAX_LISTED = 20


def ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return f"{numerator}/{denominator}（—）"
    return f"{numerator}/{denominator}（{100 * numerator / denominator:.1f}%）"


def _counts(values, unit: str = "個 Run") -> str:
    return "、".join(f"{v if v is not None else '未回報'}（{n} {unit}）" for v, n in sorted(Counter(values).items(), key=lambda kv: str(kv[0])))


def render(experiment: Experiment, records: list[RunRecord], rows: list[ComponentResult]) -> str:
    cfg = experiment.config
    lines = [
        f"# 評核報告：{cfg.id}", "",
        "## 實驗設定", "",
        f"- 實驗 id：{cfg.id}",
        f"- 文章：{len(cfg.articles.ids)} 篇（{'、'.join(cfg.articles.ids)}）",
        f"- Direction：{'、'.join(cfg.directions)}",
        f"- 輸入版本：{'、'.join(sorted({r.input_version for r in records}))}",
        "", "## Approach 設定", "",
    ]
    for planned in experiment.approaches:
        lines += approach_lines(planned, [r for r in records if r.approach == planned.config.id])
    lines += ["## Prompt", ""]
    prompts = Counter((r.prompt_id, r.prompt_hash) for r in records)
    lines += [f"- {prompt_id}：{prompt_hash}（{n} 個 Run）" for (prompt_id, prompt_hash), n in sorted(prompts.items())]
    lines.append("")

    planned_runs = {(p.config.id, d): len(cfg.articles.ids) * p.runs for p in experiment.approaches for d in cfg.directions}
    pairs = list(planned_runs) + sorted({(r.approach, r.direction) for r in records} - set(planned_runs))   # 紀錄裡有、設定裡沒有的也列出
    for approach_id, direction in pairs:
        section = [r for r in records if r.approach == approach_id and r.direction == direction]
        section_rows = [r for r in rows if r.approach == approach_id and r.direction == direction]
        lines += section_lines(approach_id, direction, planned_runs.get((approach_id, direction)), section, section_rows)
    return "\n".join(lines).rstrip("\n") + "\n"


def approach_lines(planned: PlannedApproach, records: list[RunRecord]) -> list[str]:
    a = planned.config
    hashes = Counter(r.config_hash for r in records)
    hash_text = "、".join(
        f"{h}（{n} 個 Run{'' if h == planned.config_hash else '，與目前設定不同'}）" for h, n in sorted(hashes.items())
    ) or f"{planned.config_hash}（目前設定，還沒有 Run）"
    temperature = "API 預設" if a.temperature is None else a.temperature
    max_tokens = "API 預設" if a.max_output_tokens is None else a.max_output_tokens
    return [
        f"### {a.id}", "",
        f"- 模型家族：{a.family}；provider：{a.provider}；模型：{a.model}",
        f"- 請求模式：{a.request_mode}；temperature：{temperature}；max_output_tokens：{max_tokens}；timeout：{a.timeout_seconds} 秒；併發：{a.concurrency}",
        f"- 重試：最多 {a.retry.max_attempts} 次嘗試，退避起點 {a.retry.base_delay_seconds} 秒",
        f"- 每篇每個 Direction 的 Run 次數：{planned.runs}",
        f"- 設定 hash：{hash_text}",
        f"- 實際回應的模型版本：{_counts(r.model_version for r in records) or '無'}",
        "",
    ]


def section_lines(approach_id: str, direction: str, planned: int | None, records: list[RunRecord],
                  rows: list[ComponentResult]) -> list[str]:
    n = len(records)
    by_outcome = Counter(r.outcome for r in records)
    reasons = Counter(r.reason for r in records if r.outcome is Outcome.BLOCK)
    reason_text = "、".join(f"{reason} {count}" for reason, count in sorted(reasons.items(), key=lambda kv: str(kv[0]))) or "無"
    if planned is None:
        recorded = f"不在目前的實驗設定內，已有紀錄 {n}"
    else:
        recorded = f"計畫 {planned}，已有紀錄 {n}" + ("（未完成）" if n < planned else "")
    omissions = sum(1 for r in rows for f in r.findings if f.subtype == OMISSION)
    checked_runs = len({RunKey.of(r) for r in rows})
    n_components = sum(1 for r in rows if r.source_type is not None)
    others = "、".join(f"{o} {ratio(by_outcome[Outcome(o)], n)}" for o in ("malformed", "truncated", "error"))
    return [
        f"## {approach_id} × {direction}", "",
        f"- Run：{recorded}",
        f"- Block 率：{ratio(by_outcome[Outcome.BLOCK], n)}；原因分佈：{reason_text}",
        f"- Refusal 率：{ratio(by_outcome[Outcome.REFUSAL], n)}",
        f"- 其他失敗：{others}",
        f"- 漏段數：{omissions}（已檢查 {checked_runs} 個 ok 的 Run、共 {n_components} 個 Component）",
        "",
    ]


def run(args) -> int:
    experiment = load_experiment(args.config)
    out = OutputDir(args.data_dir, experiment.config.id)
    records = out.read_runs()
    if not records:
        raise InputError(f"{out.runs_path}：沒有 Run 紀錄，先執行 translate")
    rows = out.read_components()
    checked = {RunKey.of(r) for r in rows}
    unchecked = [r for r in records if r.outcome is Outcome.OK and RunKey.of(r) not in checked]
    if unchecked:
        problems = [f"ok 的 Run 還沒有 check 結果，先執行 check：{r.article_id} {r.approach} {r.direction} r{r.run_index}"
                    for r in unchecked[:MAX_LISTED]]
        if len(unchecked) > MAX_LISTED:
            problems.append(f"…另有 {len(unchecked) - MAX_LISTED} 個")
        raise InputError(problems)
    out.write_report(render(experiment, records, rows))
    print(f"已寫入 {out.report_path}（{len(records)} 個 Run）")
    return 0
