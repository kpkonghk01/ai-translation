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
import functools
import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .articles import (TAGS_ID, TAGS_TYPE, TITLE_ID, TITLE_TYPE, apply_variant, expected_components, load_article, source_texts,
                       text_of)
from .config import Experiment, length_ranges, load_experiment
from .errors import InputError
from .outcome import looks_like_refusal
from .schemas import (DUPLICATE_COMPONENT, EXTRA_COMPONENT, OMISSION, RESIDUAL_HAN, RESIDUAL_TRADITIONAL, TERM_MISMATCH, TYPE_CHANGED,
                      Article, CheckMeta,
                      CheckWarning, ComponentResult, Dimension, Direction, Finding, LengthRatioRange, Outcome, RunKey, Severity,
                      TermEntry, Translation, WarningKind)
from .store import OutputDir
from .terms import entries_in, has_allowed_rendering, matched_text
from .textrules import (contains_han, han_runs, missing_digit_tokens, missing_numbers, nonspace_length, quote_count,
                        residual_traditional)

MAX_LISTED_NUMBERS = 10
# 實作這些規則的原始碼：它們的內容決定 rules_version
_RULE_SOURCES = ("check.py", "textrules.py", "terms.py", "articles.py", "outcome.py")


@functools.cache
def rules_version() -> str:
    """check 規則的版本，由實作這些規則的原始碼的 hash 決定：改了任何一個檔案，舊的 check 結果就視為過期，
    不靠人記得手動改版本號。不影響規則的改動也會令結果過期；重跑 check 只要幾秒，不呼叫 API。"""
    digest = hashlib.sha256()
    for name in _RULE_SOURCES:
        digest.update((Path(__file__).parent / name).read_bytes())
    return f"deterministic-{digest.hexdigest()[:12]}"


@dataclass(frozen=True)
class _Translated:
    type: str
    fields: dict


def _finding(dimension: Dimension, subtype: str, severity: Severity, reason: str, *, source_span: str = "",
             target_span: str = "") -> Finding:
    return Finding(source="deterministic", dimension=dimension, subtype=subtype, severity=severity, reason=reason,
                   source_span=source_span, target_span=target_span)


def _warning(kind: WarningKind, detail: str) -> CheckWarning:
    return CheckWarning(kind=kind, detail=detail)


def _omission() -> Finding:
    return _finding(Dimension.ACCURACY, OMISSION, Severity.MAJOR, "譯文缺少這個 Component")


def _reserved_translations(translation: Translation) -> dict[str, _Translated]:
    """title 與 tags 沒有 id：空白的 title、空的 tags 視同缺少。"""
    found = {}
    if translation.title and translation.title.strip():
        found[TITLE_ID] = _Translated(TITLE_TYPE, {"title": translation.title})
    if translation.tags:
        found[TAGS_ID] = _Translated(TAGS_TYPE, {"tags": translation.tags})
    return found


def _content_findings(direction: Direction, source_text: str, translated_text: str, entries: Sequence[TermEntry]) -> list[Finding]:
    findings = []
    applicable = entries_in(entries, source_text, direction)
    for entry in applicable:
        if not has_allowed_rendering(translated_text, entry.renderings(direction)):
            findings.append(_finding(Dimension.TERMINOLOGY, TERM_MISMATCH, entry.severity, "譯文沒有用 Term List 指定的譯法",
                                     source_span=matched_text(source_text, entry.source)))
    if direction is Direction.TRAD_TO_EN:
        findings += [_finding(Dimension.LOCALE_FORMAT, RESIDUAL_HAN, Severity.MINOR, "譯文殘留漢字", target_span=run)
                     for run in han_runs(translated_text)]
    elif direction is Direction.TRAD_TO_SIMPLIFIED:
        # Term List 的例外只對原文有那個詞的 Component 有效
        exempt = frozenset(char for entry in applicable for exception in entry.simplified_exceptions for char in exception)
        findings += [_finding(Dimension.LOCALE_FORMAT, RESIDUAL_TRADITIONAL, Severity.MINOR, "譯文殘留 OpenCC t2s 會改動的繁體字",
                              target_span=char)
                     for char in residual_traditional(translated_text, exempt)]
    return findings


def _content_warnings(direction: Direction, source_text: str, translated_text: str, length_range: LengthRatioRange) -> list[CheckWarning]:
    warnings = []
    source_length, translated_length = nonspace_length(source_text), nonspace_length(translated_text)
    if source_length:
        ratio = translated_length / source_length
        if not length_range.min <= ratio <= length_range.max:
            warnings.append(_warning(WarningKind.LENGTH_RATIO,
                                     f"譯文與原文的長度比是 {ratio:.2f}，範圍 {length_range.min}–{length_range.max}"))
        # 「遠短於原文」以這個 Direction 正常範圍的下限為準：低於它，又像拒絕語，才列為疑似 Refusal
        if ratio < length_range.min and looks_like_refusal(translated_text):
            warnings.append(_warning(WarningKind.SUSPECTED_REFUSAL, "譯文像拒絕語，而且遠短於原文"))
    missing = (missing_numbers if direction is Direction.TRAD_TO_EN else missing_digit_tokens)(source_text, translated_text)
    if missing:
        warnings.append(_warning(WarningKind.NUMBER_MISSING,
                                 f"原文的 {len(missing)} 個數字在譯文找不到：{'、'.join(missing[:MAX_LISTED_NUMBERS])}"))
    source_quotes, translated_quotes = quote_count(source_text), quote_count(translated_text)
    if source_quotes != translated_quotes:
        warnings.append(_warning(WarningKind.QUOTE_COUNT, f"原文有 {source_quotes} 段直接引語，譯文有 {translated_quotes} 段"))
    # 原文沒有漢字的（例如只有英文或數字）本來就不用翻譯，相同不算沒翻譯。繁→簡留著原文的繁體字，
    # 殘留字的 Finding 已經記了，不另列警示
    if direction is Direction.TRAD_TO_EN and translated_text == source_text and contains_han(source_text):
        warnings.append(_warning(WarningKind.UNCHANGED_TRANSLATABLE, "譯文與原文完全相同"))
    return warnings


def check_run(key: RunKey, article: Article, translation: Translation, entries: Sequence[TermEntry],
              ranges: Mapping[Direction, LengthRatioRange]) -> list[ComponentResult]:
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
                findings += _content_findings(key.direction, source_text, translated_text, entries)
                warnings += _content_warnings(key.direction, source_text, translated_text, ranges[key.direction])
            elif found.type == expected.type and found.fields != source_fields[expected.id]:
                # 不翻譯的 Component 要原樣保留；type 被改的已經記為 Finding，不重覆列警示
                warnings.append(_warning(WarningKind.UNTRANSLATABLE_CHANGED, "不翻譯的 Component 內容被改動"))
        rows.append(row(expected.id, expected.type, findings, warnings))

    if TAGS_ID in reserved and not article.tags:   # 沒有送 tags（例如 main-only）、譯文卻有
        rows.append(row(TAGS_ID, None, [_finding(Dimension.LOCALE_FORMAT, EXTRA_COMPONENT, Severity.MAJOR,
                                                 "譯文多出原文沒有的 tags")]))
    source_ids = {c.id for c in article.components}
    for component_id in real:
        if component_id not in source_ids:
            rows.append(row(component_id, None, [_finding(Dimension.LOCALE_FORMAT, EXTRA_COMPONENT, Severity.MAJOR,
                                                          f"譯文多出原文沒有的 Component（type {real[component_id].type}）")]))
    return rows


def current_check_meta(experiment: Experiment) -> CheckMeta:
    """這個實驗現在的設定下，check 應該用的規則版本與外部輸入。"""
    return CheckMeta(rules_version=rules_version(), term_list_hash=experiment.term_list.check_hash,
                     length_ratio=length_ranges(experiment.config))


def require_same_term_list(experiment: Experiment, ok_records) -> None:
    """check 用的 Term List 要與翻譯時注入 prompt 的是同一份。不同的話，check 會拿模型沒看過的條目去扣分，
    或漏掉模型被要求用的譯法，所以停止。只改嚴重度、類別或備註不影響 prompt，不在此限。"""
    mismatched = [r for r in ok_records if not r.is_baseline and r.term_list_hash != experiment.term_list.prompt_hash]
    if mismatched:
        raise InputError(f"{len(mismatched)} 個 ok 的 Run 翻譯時用的 Term List 與現在的不同（原文詞或譯法有改，或加了、拿掉了 Term List）。"
                         "請把 Term List 改回去，或用新的實驗 id 重新翻譯")


def handle(args) -> int:
    experiment = load_experiment(args.config)
    out = OutputDir(args.data_dir, experiment.config.id)
    records = out.require_runs()
    ok_records = [r for r in records if r.outcome is Outcome.OK]
    require_same_term_list(experiment, ok_records)
    ranges = length_ranges(experiment.config)

    articles, rows = {}, []
    for record in ok_records:
        if record.article_id not in articles:
            articles[record.article_id] = load_article(experiment.config.articles.dir, record.article_id)
        sent = apply_variant(articles[record.article_id], record.input_variant)   # 只核對送出去的那一份
        rows += check_run(RunKey.of(record), sent, out.read_translation(record), experiment.term_list.entries, ranges)

    out.write_components(rows)
    out.write_check_meta(current_check_meta(experiment))   # 結果寫好之後才寫：中途中斷時 report 會發現它不齊
    omissions = sum(r.n_omissions for r in rows)
    print(f"已檢查 {len(ok_records)} 個 ok 的 Run（共 {len(records)} 個 Run），{len(rows)} 個 Component；漏段 {omissions}")
    return 0
