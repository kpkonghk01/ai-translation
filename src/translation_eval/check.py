"""check：對每個 ok 的 Run 做 Deterministic check。

用法：
  uv run --locked python -I -m translation_eval check --config <實驗設定> [--data-dir data]

每次都整份重寫 components.jsonl，所以重跑是安全的。檢查分兩層：
  結構：缺少的 Component 記為 Accuracy／omission major，計入報告的漏段數；多出、重覆和 type 被改屬於結構完整性，
        記為 Locale & Format。
  內容：Terminology（譯文沒有 Term List 指定的譯法）和繁→英殘留的漢字，都是計分的 Finding。
        長度比例、數字、直接引語、疑似 Refusal、沒翻譯、不翻譯的 Component 被改動，程式判斷不夠可靠，
        只產生警示（Warning）：交人手看，不計分。
"""
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from .articles import TAGS_ID, TITLE_ID, expected_components, load_article, source_texts, text_of
from .config import Experiment, load_experiment
from .outcome import looks_like_refusal
from .schemas import (DEFAULT_LENGTH_RATIO, DUPLICATE_COMPONENT, EXTRA_COMPONENT, OMISSION, RESIDUAL_HAN, TERM_MISMATCH, TYPE_CHANGED,
                      Article, CheckMeta, CheckWarning, ComponentResult, Dimension, Direction, Finding, LengthRatioRange, Outcome,
                      RunKey, Severity, TermList, Translation, WarningKind, length_ranges)
from .store import OutputDir
from .terms import entries_in, has_allowed_rendering
from .textrules import contains_han, han_runs, missing_numbers, nonspace_length, quote_count

# check 的規則有改（新增、刪除或改動任何一條）就要改這個版本：report 靠它發現 check 的結果已經過期
RULES_VERSION = "deterministic.v2"

# 譯文短於原文的這個比例、又像拒絕語，才列為疑似 Refusal：正常的英文譯文比中文原文長，這樣短幾乎一定不是譯文
REFUSAL_MAX_LENGTH_RATIO = 0.5
MAX_LISTED_NUMBERS = 10


@dataclass(frozen=True)
class _Translated:
    type: str
    fields: dict


def _finding(dimension: Dimension, subtype: str, severity: Severity, reason: str, *, source_span: str = "",
             target_span: str = "") -> Finding:
    return Finding(source="deterministic", dimension=dimension, subtype=subtype, severity=severity, reason=reason,
                   source_span=source_span, target_span=target_span)


def _omission() -> Finding:
    return _finding(Dimension.ACCURACY, OMISSION, Severity.MAJOR, "譯文缺少這個 Component")


def _reserved_translations(translation: Translation) -> dict[str, _Translated]:
    """title 與 tags 沒有 id：空白的 title、空的 tags 視同缺少。"""
    found = {}
    if translation.title and translation.title.strip():
        found[TITLE_ID] = _Translated("title", {"title": translation.title})
    if translation.tags:
        found[TAGS_ID] = _Translated("tags", {"tags": translation.tags})
    return found


def _content_findings(key: RunKey, source_text: str, translated_text: str, term_list: TermList | None) -> list[Finding]:
    findings = []
    for entry in entries_in(term_list, source_text, key.direction):
        if not has_allowed_rendering(translated_text, entry.translations[key.direction]):
            findings.append(_finding(Dimension.TERMINOLOGY, TERM_MISMATCH, entry.severity, "譯文沒有用 Term List 指定的譯法",
                                     source_span=entry.source))
    if key.direction is Direction.TRAD_TO_EN:
        findings += [_finding(Dimension.LOCALE_FORMAT, RESIDUAL_HAN, Severity.MINOR, "譯文殘留漢字", target_span=run)
                     for run in han_runs(translated_text)]
    return findings


def _content_warnings(source_text: str, translated_text: str, length_range: LengthRatioRange) -> list[CheckWarning]:
    warnings = []
    source_length, translated_length = nonspace_length(source_text), nonspace_length(translated_text)
    if source_length:
        ratio = translated_length / source_length
        if not length_range.min <= ratio <= length_range.max:
            warnings.append(CheckWarning(kind=WarningKind.LENGTH_RATIO,
                                         detail=f"譯文與原文的長度比是 {ratio:.2f}，範圍 {length_range.min}–{length_range.max}"))
        if ratio < REFUSAL_MAX_LENGTH_RATIO and looks_like_refusal(translated_text):
            warnings.append(CheckWarning(kind=WarningKind.SUSPECTED_REFUSAL, detail="譯文像拒絕語，而且遠短於原文"))
    missing = missing_numbers(source_text, translated_text)
    if missing:
        warnings.append(CheckWarning(kind=WarningKind.NUMBER_MISSING,
                                     detail=f"原文的 {len(missing)} 個數字在譯文找不到：{'、'.join(missing[:MAX_LISTED_NUMBERS])}"))
    source_quotes, translated_quotes = quote_count(source_text), quote_count(translated_text)
    if source_quotes != translated_quotes:
        warnings.append(CheckWarning(kind=WarningKind.QUOTE_COUNT, detail=f"原文有 {source_quotes} 段直接引語，譯文有 {translated_quotes} 段"))
    # 原文沒有漢字的（例如只有英文或數字）本來就不用翻譯，相同不算沒翻譯
    if translated_text == source_text and contains_han(source_text):
        warnings.append(CheckWarning(kind=WarningKind.UNCHANGED_TRANSLATABLE, detail="譯文與原文完全相同"))
    return warnings


def check_run(key: RunKey, article: Article, translation: Translation, term_list: TermList | None = None,
              ranges: Mapping[Direction, LengthRatioRange] = DEFAULT_LENGTH_RATIO) -> list[ComponentResult]:
    reserved = _reserved_translations(translation)
    counts = Counter(c.id for c in translation.components)
    real: dict[str, _Translated] = {}
    for c in translation.components:
        real.setdefault(c.id, _Translated(c.type, c.model_dump()))
    texts = source_texts(article)
    source_fields = {c.id: c.model_dump() for c in article.components}

    def row(component_id: str, source_type: str | None, findings: list[Finding],
            warnings: list[CheckWarning] | None = None) -> ComponentResult:
        return ComponentResult(**key.model_dump(), component_id=component_id, source_type=source_type, findings=findings,
                               warnings=warnings or [])

    rows = []
    for expected in expected_components(article):
        is_reserved = expected.id in (TITLE_ID, TAGS_ID)
        found = reserved.get(expected.id) if is_reserved else real.get(expected.id)
        findings, warnings = [], []
        if found is None:
            findings.append(_omission())
        else:
            if not is_reserved:
                if found.type != expected.type:
                    findings.append(_finding(Dimension.LOCALE_FORMAT, TYPE_CHANGED, Severity.MINOR,
                                             f"type 由 {expected.type} 改成 {found.type}"))
                if counts[expected.id] > 1:
                    findings.append(_finding(Dimension.LOCALE_FORMAT, DUPLICATE_COMPONENT, Severity.MAJOR,
                                             f"譯文有 {counts[expected.id]} 個同一 id 的 Component"))
            source_text = texts[expected.id]
            if source_text is not None:
                translated_text = text_of(expected.type, found.fields) or ""
                findings += _content_findings(key, source_text, translated_text, term_list)
                warnings += _content_warnings(source_text, translated_text, ranges[key.direction])
            elif found.type == expected.type and found.fields != source_fields[expected.id]:
                # 不翻譯的 Component 要原樣保留；type 被改的已經記為 Finding，不重覆列警示
                warnings.append(CheckWarning(kind=WarningKind.UNTRANSLATABLE_CHANGED, detail="不翻譯的 Component 內容被改動"))
        rows.append(row(expected.id, expected.type, findings, warnings))

    source_ids = {c.id for c in article.components}
    for component_id in real:
        if component_id not in source_ids:
            rows.append(row(component_id, None, [_finding(Dimension.LOCALE_FORMAT, EXTRA_COMPONENT, Severity.MAJOR,
                                                          f"譯文多出原文沒有的 Component（type {real[component_id].type}）")]))
    return rows


def current_check_meta(experiment: Experiment) -> CheckMeta:
    """這個實驗現在的設定下，check 應該用的規則版本與外部輸入。"""
    return CheckMeta(rules_version=RULES_VERSION, term_list_hash=experiment.term_list.hash if experiment.term_list else None,
                     length_ratio=length_ranges(experiment.config))


def handle(args) -> int:
    experiment = load_experiment(args.config)
    out = OutputDir(args.data_dir, experiment.config.id)
    records = out.require_runs()
    term_list = experiment.term_list.terms if experiment.term_list else None
    ranges = length_ranges(experiment.config)

    articles, rows, n_checked = {}, [], 0
    for record in records:
        if record.outcome is not Outcome.OK:
            continue
        if record.article_id not in articles:
            articles[record.article_id] = load_article(experiment.config.articles.dir, record.article_id)
        rows += check_run(RunKey.of(record), articles[record.article_id], out.read_translation(record), term_list, ranges)
        n_checked += 1

    out.write_components(rows)
    out.write_check_meta(current_check_meta(experiment))   # 結果寫好之後才寫：中途中斷時 report 會發現它不齊
    omissions = sum(r.n_omissions for r in rows)
    print(f"已檢查 {n_checked} 個 ok 的 Run（共 {len(records)} 個 Run），{len(rows)} 個 Component；漏段 {omissions}")
    return 0
