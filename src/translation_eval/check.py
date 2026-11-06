"""check：對每個 ok 的 Run 做 Deterministic check。目前檢查 Component 的 id 與 type 是否與原文一致。

用法：
  uv run --locked python -I -m translation_eval check --config <實驗設定> [--data-dir data]

每次都整份重寫 components.jsonl，所以重跑是安全的。缺少的 Component 記為 Accuracy／omission major，
計入報告的漏段數；多出、重覆和 type 被改屬於結構完整性，記為 Locale & Format。
"""
from collections import defaultdict

from .articles import TAGS_ID, TITLE_ID, expected_components, load_article
from .config import load_experiment
from .errors import InputError
from .schemas import Article, ComponentResult, Dimension, Finding, Outcome, RunKey, Severity, Translation
from .store import OutputDir

OMISSION = "omission"
EXTRA = "extra-component"
DUPLICATE = "duplicate-component"
TYPE_CHANGED = "type-changed"


def _finding(dimension: Dimension, subtype: str, severity: Severity, reason: str) -> Finding:
    return Finding(source="deterministic", dimension=dimension, subtype=subtype, severity=severity, reason=reason)


def _omission() -> Finding:
    return _finding(Dimension.ACCURACY, OMISSION, Severity.MAJOR, "譯文缺少這個 Component")


def check_run(key: RunKey, article: Article, translation: Translation) -> list[ComponentResult]:
    """title 與 tags 沒有 id：空白的 title、空的 tags 視同缺少。"""
    virtual_present = {TITLE_ID: bool(translation.title and translation.title.strip()), TAGS_ID: bool(translation.tags)}
    translated_types = defaultdict(list)
    for c in translation.components:
        translated_types[c.id].append(c.type)

    def row(component_id: str, source_type: str | None, findings: list[Finding]) -> ComponentResult:
        return ComponentResult(**key.model_dump(), component_id=component_id, source_type=source_type, findings=findings)

    rows = []
    for expected in expected_components(article):
        findings = []
        if expected.id in virtual_present:
            if not virtual_present[expected.id]:
                findings.append(_omission())
        elif expected.id not in translated_types:
            findings.append(_omission())
        else:
            types = translated_types[expected.id]
            if types[0] != expected.type:
                findings.append(_finding(Dimension.LOCALE_FORMAT, TYPE_CHANGED, Severity.MINOR,
                                         f"type 由 {expected.type} 改成 {types[0]}"))
            if len(types) > 1:
                findings.append(_finding(Dimension.LOCALE_FORMAT, DUPLICATE, Severity.MAJOR,
                                         f"譯文有 {len(types)} 個同一 id 的 Component"))
        rows.append(row(expected.id, expected.type, findings))

    source_ids = {c.id for c in article.components}
    for component_id, types in translated_types.items():
        if component_id not in source_ids:
            rows.append(row(component_id, None, [_finding(Dimension.LOCALE_FORMAT, EXTRA, Severity.MAJOR,
                                                          f"譯文多出原文沒有的 Component（type {types[0]}）")]))
    return rows


def run(args) -> int:
    experiment = load_experiment(args.config)
    out = OutputDir(args.data_dir, experiment.config.id)
    records = out.read_runs()
    if not records:
        raise InputError(f"{out.runs_path}：沒有 Run 紀錄，先執行 translate")

    articles, rows, n_checked = {}, [], 0
    for record in records:
        if record.outcome is not Outcome.OK:
            continue
        if record.article_id not in articles:
            articles[record.article_id] = load_article(experiment.config.articles.dir, record.article_id)
        rows += check_run(RunKey.of(record), articles[record.article_id], out.read_translation(record))
        n_checked += 1

    out.write_components(rows)
    omissions = sum(1 for r in rows for f in r.findings if f.subtype == OMISSION)
    print(f"已檢查 {n_checked} 個 ok 的 Run（共 {len(records)} 個 Run），{len(rows)} 個 Component；漏段 {omissions}")
    return 0
