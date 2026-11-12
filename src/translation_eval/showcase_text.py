"""展示報告（含原文與譯文）：給人看「每個數字背後是什麼樣」。**含真實文章的原文與譯文片段，只在本機看，不要轉發。**
要給別人看的是 showcase.html（只有計數，沒有文章內容）；這一頁是另一個檔案，showcase 不會因為它而有任何文章內容。

用法：
  uv run --locked python -I -m translation_eval showcase-text --config <實驗設定> [--data-dir data]

寫到 <data-dir>/experiments/<實驗 id>/showcase-text.html（在 data/ 底下，不進 git）。內容：
- 成功的例子：同一段原文，各模型（第 1 個方向、完整文章、第 1 次）的譯文並排。優先用對照文章（不敏感的）；
  文章要所有模型都成功翻譯才能比，沒有就換下一篇。
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
from .schemas import Article, Direction, Outcome, RunRecord, Translation
from .report_text import DIRECTION_LABELS, group_label
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


def text_block(label: str, title: str, text: str) -> str:
    return (f'<div class="text-block"><p class="meta">{escape(label)}</p>'
            f'<p class="headline">{escape(title)}</p><p class="passage">{escape(text)}</p></div>')


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


def render_text(loaded: Loaded, read_translation) -> str:
    shown = shown_records(loaded.experiment, loaded.records, loaded.group_of)
    body = ("<header><h1>AI 翻譯的例子（含原文與譯文）</h1>"
            f'<p class="status wait">{escape(WARNING)}</p></header>'
            f"{comparison_html(loaded, shown, read_translation)}{failures_html(loaded, shown)}"
            f"<footer><p>實驗：{escape(loaded.experiment.config.id)}。文章編號沒有放在這一頁。</p></footer>")
    return (f'<!doctype html>\n<html lang="zh-Hant"><meta charset="utf-8"><title>AI 翻譯的例子（含原文與譯文）</title>'
            f"<style>{STYLE}{TEXT_STYLE}</style><body><main>{body}</main></body></html>\n")


def handle(args) -> int:
    loaded = load(args, with_articles=True)
    html = render_text(loaded, loaded.out.read_translation)
    loaded.out.write_showcase_text(html)   # 都算好才寫：中途失敗不會留下半份頁面，也不會改到舊的
    print(f"已寫入 {loaded.out.showcase_text_path}（含真實文章的原文與譯文，只在本機看，不要轉發）")
    return 0


TEXT_STYLE = """
.compare { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:14px; }
.text-block { background:var(--panel); border:1px solid var(--rule); border-radius:8px; padding:12px 14px; display:grid; gap:4px; align-content:start; }
.headline { font-weight:700; margin:0; } .passage { margin:0; white-space:pre-wrap; }
"""
