"""展示報告（含原文與譯文）：給人看「每個數字背後是什麼樣」。**含真實文章的原文與譯文片段，只在本機看，不要轉發。**
要給別人看的是 showcase.html（只有計數，沒有文章內容）；這一頁是另一個檔案，showcase 不會因為它而有任何文章內容。

用法：
  uv run --locked python -I -m translation_eval showcase-text --config <實驗設定> [--judged-in <實驗設定>] [--data-dir data]

寫到 <data-dir>/experiments/<實驗 id>/showcase-text.html（在 data/ 底下，不進 git）。內容：
- 成功的例子：同一段原文，各模型（第 1 個方向、完整文章、第 1 次）的譯文並排。優先用對照文章（不敏感的）；
  文章要所有模型都成功翻譯才能比，沒有就換下一篇。
- 被寫輕的例子（有 --judged-in 才有）：Judge 評核過的實驗（通常是抽樣實驗；要有 judging、ground truth 和事實核對的紀錄，而且已經 check）裡，
  每個（方向、題材組）一個小節（方向依實驗設定的順序，組是一般題材在前），小節裡每種淡化類型取一個被判淡化的事實（取這種類型在這一格的紀錄裡的第一個，
  不是挑過的；只取完整文章、譯文有這一段的；人改判成沒淡化的不取，人改判的類型以人為準）：要保留的事實、原文與譯文（同一個 Component）、
  Judge 的理由（兩次判的類型一致就只寫一句；不一致才兩句都寫，各註明判成哪一類；不放 Judge 的引用，引用多半就是上面的整句譯文）、白話的類型、有沒有人審過。沒有單一類型的（Judge 兩次判的類型不同，或人改判時沒有寫類型）另成一張，排在小節最後。
  沒有被判淡化的事實的格子也有小節，寫明沒有。Judge 的判定不一定對，所以頁面寫明初步、尚未經人審的，要讀的人自己對照原文與譯文。
- 沒有成功的例子：showcase 的每一張例子卡（被擋、空內容、輸出格式壞、出錯……），多附這一個 Run 的文章的原文片段，並說明回來的是什麼。
  provider 回應裡的字不放：說明由程式按結果類別寫。
片段：第一個 40 至 160 字的段落（paragraph 或 highlight）；沒有就取第一個有字的，超過 160 字的切掉並標「…」。
譯文取同一個 Component；原文被切掉時，譯文也只取開頭 160 字（切的位置不會對齊原文）。
終端只印路徑，不印任何文章內容。沒有外部資源（字型、script、網址）。
"""
from dataclasses import dataclass
from html import escape

from .articles import text_of
from .ground_truth import article_roles
from .judged import Judged, load_judged
from .review_decisions import Decisions
from .review_items import ItemKind, ItemStatus, ReviewItem
from .schemas import (Article, Direction, FAILING_VERDICTS, InputVariant, Outcome, ReviewAction, ReviewDecision, RunRecord, Translation,
                      WeakeningType)
from .report_text import DIRECTION_LABELS, WEAKENING_LABELS, group_label, ordered_groups
from .showcase import STYLE, Loaded, example_html, first_full_runs, load, ordered_kinds, runs_by_kind, shown_records

EXCERPT_MIN, EXCERPT_MAX = 40, 160
EXCERPT_TYPES = ("paragraph", "highlight")
CUT = "…"
MISSING = "（這個模型的譯文沒有這一段）"
# 沒有成功的 Run：回來的是什麼（程式按結果類別寫，不用 provider 回應裡的字）
WHAT_CAME_BACK = {
    Outcome.BLOCK: "沒有任何譯文。",
    Outcome.REFUSAL: "回來的不是譯文，這裡不放它的內容。",
    Outcome.EMPTY: "回來的是空的，沒有任何輸出。",
    Outcome.MALFORMED: "模型有回覆，但格式不對，這裡不放它的內容。",
    Outcome.TRUNCATED: "譯文在中途被截斷，這裡不放。",
    Outcome.ERROR: "沒有拿到可用的回應。",
}
NO_TYPE_GIVEN = "沒有標類型"   # Judge 判為被寫輕或不見，但沒有寫類型
NO_SINGLE_TYPE = "沒有單一類型"   # Judge 兩次都判被淡化但類型不同，或人改判時沒有寫類型：沒有單一的白話類型
WARNING = ("這一頁含真實文章的原文與譯文，只在本機看，不要轉發，也不要上傳到任何地方。"
           "要給別人看，請用只有計數的 showcase.html。")


@dataclass(frozen=True)
class Excerpt:
    component_id: str | None
    text: str
    cut: bool = False   # 原文太長被切掉：譯文也要切


def passage(article: Article) -> Excerpt:
    """文章的片段：第一個長度合適的段落；沒有就取第一個有字的，太長的切掉。"""
    candidates = [(c.id, text_of(c.type, c.model_dump())) for c in article.components if c.type in EXCERPT_TYPES]
    candidates = [(component_id, text) for component_id, text in candidates if text]
    for component_id, text in candidates:
        if EXCERPT_MIN <= len(text) <= EXCERPT_MAX:
            return Excerpt(component_id, text)
    if not candidates:
        return Excerpt(None, "")
    component_id, text = candidates[0]
    return Excerpt(component_id, text[:EXCERPT_MAX] + CUT, cut=True) if len(text) > EXCERPT_MAX else Excerpt(component_id, text)


def translated_passage(translation: Translation, excerpt: Excerpt) -> str:
    component = next((c for c in translation.components if c.id == excerpt.component_id), None)
    if component is None:
        return MISSING
    text = text_of(component.type, component.model_dump()) or ""
    return text[:EXCERPT_MAX] + CUT if excerpt.cut and len(text) > EXCERPT_MAX else text


def text_block(label: str, title: str | None, text: str) -> str:
    """title 是 None 就沒有標題那一行（只放一段文字的）。"""
    headline = "" if title is None else f'<p class="headline">{escape(title)}</p>'
    return f'<div class="text-block"><p class="meta">{escape(label)}</p>{headline}<p class="passage">{escape(text)}</p></div>'


def success_article(loaded: Loaded, shown: list[RunRecord], direction: Direction) -> str | None:
    """所有模型都成功翻譯的第一篇文章，優先對照文章（不敏感的）。"""
    cfg = loaded.experiment.config
    done = {(r.approach, r.article_id) for r in first_full_runs(shown) if r.outcome is Outcome.OK and r.direction == direction}
    complete = [i for i in cfg.articles.ids if all((p.id, i) in done for p in loaded.experiment.approaches)]
    roles = article_roles(cfg.ground_truth) if cfg.ground_truth else {}
    return next((i for i in complete if roles.get(i) == "control"), next(iter(complete), None))


def comparison_html(loaded: Loaded, shown: list[RunRecord], read_translation) -> str:
    cfg = loaded.experiment.config
    direction = cfg.directions[0]
    article_id = success_article(loaded, shown, direction)
    if article_id is None:
        return ""
    article = loaded.articles[article_id]
    excerpt = passage(article)
    by_model = {r.approach: r for r in first_full_runs(shown) if r.article_id == article_id and r.direction == direction}
    rows = []
    for planned in loaded.experiment.approaches:
        translation = read_translation(by_model[planned.id])
        rows.append(text_block(loaded.names[planned.id], translation.title or "", translated_passage(translation, excerpt)))
    group = group_label(loaded.group_of[article_id])
    return ("<section><h2>成功的例子：同一段原文，各模型怎麼翻</h2>"
            f'<p class="note">{escape(DIRECTION_LABELS.get(direction, str(direction)))}、{escape(group)}的文章；所有模型都翻譯成功的第一篇（優先對照文章）。</p>'
            f'<div class="compare">{text_block("原文", article.title, excerpt.text)}{"".join(rows)}</div></section>')


def failures_html(loaded: Loaded, shown: list[RunRecord]) -> str:
    by_kind = runs_by_kind(shown)
    approach_order = {p.id: n for n, p in enumerate(loaded.experiment.approaches)}
    cards = []
    for kind in ordered_kinds(by_kind):
        if kind[0] is Outcome.OK:
            continue
        first = by_kind[kind][0]
        article = loaded.articles[first.article_id]
        excerpt = passage(article)
        extra = text_block("原文", article.title, excerpt.text) + f'<p class="meta">回來的：{escape(WHAT_CAME_BACK.get(first.outcome, ""))}</p>'
        cards.append(example_html(kind, by_kind[kind], loaded.names, approach_order, loaded.group_of, lambda record: 0, extra))
    if not cards:
        return ""
    return ('<section><h2>沒有成功的例子</h2><p class="note">每種結果各舉一個真實的 Run（取這種結果在紀錄裡的第一個，不是挑過的），附上它的原文片段。</p>'
            f'<ul class="examples">{"".join(cards)}</ul></section>')


def failing_type(item: ReviewItem, decision: ReviewDecision | None) -> tuple[bool, WeakeningType | None]:
    """這條事實最後算不算被淡化、是哪一類：人改判的以人為準（與 Gate 一樣），否則是 Judge 的最終判定。"""
    if decision is not None and decision.action is ReviewAction.OVERRIDE:
        return decision.verdict in FAILING_VERDICTS, decision.weakening_type
    return item.status is ItemStatus.FAILED, item.weakening


Cell = tuple[Direction, str]   # （方向, 題材組）


def cell_of(item: ReviewItem, judged: Judged) -> Cell:
    return item.run.direction, judged.truth.group_of(item.run.article_id)


def weakened_examples(judged: Judged) -> dict[Cell, dict[WeakeningType | None, ReviewItem]]:
    """被判淡化的事實，每一格（方向、題材組）每種類型取紀錄裡的第一個。沒有被判淡化事實的格子不在裡面。
    只取完整文章的、譯文有這一段的（程式證明的漏段沒有譯文可看）。"""
    first: dict[Cell, dict[WeakeningType | None, ReviewItem]] = {}
    for item in judged.items:
        if item.kind is not ItemKind.FACT or item.run.input_variant is not InputVariant.FULL or item.translation_text is None:
            continue
        failing, kind = failing_type(item, judged.decisions.get(item.key))
        if failing:
            first.setdefault(cell_of(item, judged), {}).setdefault(kind, item)
    return first


def weakened_cells(judged: Judged) -> list[Cell]:
    """有事實可以核對的格子：方向依實驗設定的順序，題材組依顯示順序（一般題材在前）。"""
    present = {cell_of(item, judged) for item in judged.items if item.kind is ItemKind.FACT and item.run.input_variant is InputVariant.FULL}
    groups = ordered_groups(group for _, group in present)
    return [(direction, group) for direction in judged.experiment.config.directions for group in groups if (direction, group) in present]


def review_text(decision: ReviewDecision | None) -> str:
    if decision is None:
        return "尚未經人審"
    if decision.action is ReviewAction.OVERRIDE:
        return f"人已看過，改判為 {decision.verdict.value}"
    return "人已看過，同意 Judge 的判定"


def judge_notes(item: ReviewItem) -> list[str]:
    """卡上 Judge 說的話：只取判為被寫輕或不見的請求。這些請求判的類型一致，就只寫一句理由（兩次的內容幾乎一樣，寫兩遍只會亂）；
    類型不同，每一次各寫一句並註明判成哪一類，否則看不出為什麼沒有單一類型。沒有請求判為被寫輕（人改判的）就沒有。"""
    failing = [v for v in item.verdicts if v.verdict in FAILING_VERDICTS]
    if len({v.weakening_type for v in failing}) <= 1:
        return [f"Judge 的理由：{failing[0].reason}"] if failing else []
    return [f"Judge 一次判為「{WEAKENING_LABELS.get(v.weakening_type, NO_TYPE_GIVEN)}」，理由：{v.reason}" for v in failing]


def weakened_card(item: ReviewItem, kind: WeakeningType | None, judged: Judged) -> str:
    names = judged.experiment.display_names()
    group = group_label(judged.truth.group_of(item.run.article_id))
    where = f"{names[item.run.approach]}、{DIRECTION_LABELS.get(item.run.direction, str(item.run.direction))}、{group}"
    heading = NO_SINGLE_TYPE if kind is None else WEAKENING_LABELS[kind]
    judge_lines = "".join(f'<p class="judge">{escape(line)}</p>' for line in judge_notes(item))
    return (f'<li class="weakened"><h4>{escape(heading)}</h4><p class="meta">{escape(where)}</p>'
            f'<p class="fact">要保留的事實：{escape(item.fact_text or "")}</p>'
            f'<div class="pair">{text_block("原文", None, item.source_text)}{text_block("譯文", None, item.translation_text or "")}</div>'
            f'{judge_lines}<p class="meta">{escape(review_text(judged.decisions.get(item.key)))}</p></li>')


def weakened_html(judged: Judged) -> str:
    """「被寫輕的例子」一節：譯出來了，但要保留的事實被寫輕或不見。"""
    heading = "<h2>被寫輕的例子：真實的譯文出了什麼問題（初步）</h2>"
    examples = weakened_examples(judged)
    if not examples:
        return f'<section>{heading}<p class="note">這一批沒有被判淡化的事實可以舉例。</p></section>'
    judges = {j.id: j.config.display_name or j.config.model for j in judged.experiment.judges}
    who = "、".join(sorted({judges[item.judge] for picked in examples.values() for item in picked.values()}))
    intro = (f"譯出來了，但原文要保留的事實被寫輕或不見。每個方向、每個題材組，每種類型取一個真實的例子（取在紀錄裡的第一個，不是挑過的）。"
             f"初步結果：被判淡化的是 AI（{who}）的判定，不一定對，請對照原文與譯文自己看。")
    cells = []
    for cell in weakened_cells(judged):
        direction, group = cell
        picked = examples.get(cell, {})
        cards = "".join(weakened_card(picked[kind], kind, judged) for kind in (*WeakeningType, None) if kind in picked)
        body = f'<ul class="weakened-cards">{cards}</ul>' if cards else '<p class="note">這一格沒有被判淡化的事實。</p>'
        cells.append(f"<h3>{escape(DIRECTION_LABELS.get(direction, str(direction)))}・{escape(group_label(group))}</h3>{body}")
    return f'<section>{heading}<p class="note">{escape(intro)}</p>{"".join(cells)}</section>'


def render_text(loaded: Loaded, read_translation, weakened: str = "") -> str:
    shown = shown_records(loaded.experiment, loaded.records, loaded.group_of)
    body = ("<header><h1>AI 翻譯的例子（含原文與譯文）</h1>"
            f'<p class="status wait">{escape(WARNING)}</p></header>'
            f"{comparison_html(loaded, shown, read_translation)}{weakened}{failures_html(loaded, shown)}"
            f"<footer><p>實驗：{escape(loaded.experiment.config.id)}。文章編號沒有放在這一頁。</p></footer>")
    return (f'<!doctype html>\n<html lang="zh-Hant"><meta charset="utf-8"><title>AI 翻譯的例子（含原文與譯文）</title>'
            f"<style>{STYLE}{TEXT_STYLE}</style><body><main>{body}</main></body></html>\n")


def handle(args) -> int:
    loaded = load(args, with_articles=True)
    weakened = weakened_html(load_judged(args.judged_in, args.data_dir)) if args.judged_in else ""
    html = render_text(loaded, loaded.out.read_translation, weakened)
    loaded.out.write_showcase_text(html)   # 都算好才寫：中途失敗不會留下半份頁面，也不會改到舊的
    print(f"已寫入 {loaded.out.showcase_text_path}（含真實文章的原文與譯文，只在本機看，不要轉發）")
    return 0


TEXT_STYLE = """
.compare { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:14px; }
.text-block { background:var(--panel); border:1px solid var(--rule); border-radius:8px; padding:12px 14px; display:grid; gap:4px; align-content:start; }
.headline { font-weight:700; margin:0; } .passage { margin:0; white-space:pre-wrap; }
.weakened-cards { display:grid; gap:14px; }
.weakened { background:var(--panel); border:1px solid var(--rule); border-radius:8px; padding:14px 16px; display:grid; gap:8px; }
.weakened h4 { margin:0; font-size:1rem; } .fact { margin:0; font-weight:700; } .judge { margin:0; color:var(--muted); font-size:.875rem; }
.pair { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:10px; }
"""
