"""讀入文章檔，並決定送給模型的內容與譯文應有的 Component。

title 與 tags 沒有 id：評核時用保留的 Component id（TITLE_ID、TAGS_ID）與其他 Component 一樣對齊。
"""
import os
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import InputError
from .loading import read_model
from .schemas import Article, InputVariant

TITLE_ID = "title"   # title 與 tags 沒有 id，評核時用這兩個保留的 id；type 也是同名的保留值
TAGS_ID = "tags"
TITLE_TYPE = "title"
TAGS_TYPE = "tags"
MAX_LISTED_UNKNOWN = 5

# Main component：承載文章內容的 Component（title 另外處理）。main-only 的輸入只送這些
MAIN_TYPES = frozenset({"highlight", "heading", "paragraph", "quote"})

# 不翻譯的 Component：原樣保留，內容不是要評核的譯文
UNTRANSLATED_TYPES = frozenset({"code", "video", "faq", "table"})
# 譯文的文字所在的欄位。gallery 的多個說明、related-article 的多個標題，以及 tags，以換行連成一段文字；
# quote 的出處（人名）也要翻譯，所以與引語連在一起。
_TEXT_FIELDS = {
    "highlight": ("text",), "heading": ("text",), "paragraph": ("text",), "quote": ("text", "source"),
    "image": ("caption",), "gallery": ("captions",), "related-article": ("titles",),
    TITLE_TYPE: ("title",), TAGS_TYPE: ("tags",),
}
_LIST_FIELDS = frozenset({"captions", "titles", "tags"})   # 其餘的文字欄位都是字串
KNOWN_TYPES = UNTRANSLATED_TYPES | {t for t in _TEXT_FIELDS if t not in (TITLE_TYPE, TAGS_TYPE)}


@dataclass(frozen=True)
class ExpectedComponent:
    id: str
    type: str


def load_article(directory: str, article_id: str) -> Article:
    article = read_model(os.path.join(directory, f"{article_id}.json"), Article)
    ids = [c.id for c in article.components]
    duplicated = sorted({i for i in ids if ids.count(i) > 1} | ({TITLE_ID, TAGS_ID} & set(ids)))
    if duplicated:
        raise InputError(f"文章 {article_id}：Component id 重覆或與保留的 id 衝突：{', '.join(duplicated)}")
    unknown_types = sorted({c.type for c in article.components} - KNOWN_TYPES)
    if unknown_types:   # 不認識的 type 沒辦法決定哪些欄位是要評核的文字，不能靜默略過
        raise InputError(f"文章 {article_id}：不認識的 Component type：{', '.join(unknown_types)}")
    malformed = [f"{c.id}（{'、'.join(names)}）" for c in article.components if (names := field_problems(c.type, c.model_dump()))]
    if malformed:   # 型別不對的文字欄位，內容規則讀不到，不能靜默當作空的
        raise InputError(f"文章 {article_id}：Component 的文字欄位型別不對：{'；'.join(malformed)}")
    # 轉換器遇到不認識的東西會記在 meta.unknown：這樣的文章可能不完整，要先檢視，不當作完整的原文去翻譯
    meta = (article.model_extra or {}).get("meta")
    unknown = meta.get("unknown") if isinstance(meta, dict) else None
    if unknown:
        listed = ", ".join(str(u)[:40] for u in unknown[:MAX_LISTED_UNKNOWN])
        raise InputError(f"文章 {article_id}：轉換器在 meta.unknown 記了 {len(unknown)} 項（{listed}），先檢視再翻譯")
    return article


def field_problems(component_type: str, fields: Mapping) -> list[str]:
    """型別不對的文字欄位名：該是字串的不是字串，該是字串清單的不是。欄位缺少不算，缺少視為空字串，
    由長度比例、Terminology 等規則呈現。載入原文和接收譯文時都用它擋掉，所以 text_of 不必處理型別不對的值。"""
    problems = []
    for name in _TEXT_FIELDS.get(component_type, ()):
        value = fields.get(name)
        if value is None:
            continue
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value) if name in _LIST_FIELDS else isinstance(value, str)
        if not ok:
            problems.append(name)
    return problems


def _as_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(v for v in value if isinstance(v, str))
    return ""


def text_of(component_type: str, fields: Mapping) -> str | None:
    """一個 Component 要評核的文字；不翻譯的 type 回傳 None。譯文的欄位缺少或型別不對時視為空字串。"""
    names = _TEXT_FIELDS.get(component_type)
    if names is None:
        return None
    return "\n".join(t for t in (_as_text(fields.get(n)) for n in names) if t)


def source_texts(article: Article) -> dict[str, str | None]:
    """原文每個 Component（含 title 與 tags 的保留 id）的文字，依出現順序。"""
    texts = {TITLE_ID: article.title}
    texts.update({c.id: text_of(c.type, c.model_dump()) for c in article.components})
    if article.tags:
        texts[TAGS_ID] = text_of(TAGS_TYPE, {"tags": article.tags})
    return texts


def article_text(article: Article) -> str:
    return "\n".join(t for t in source_texts(article).values() if t)


def apply_variant(article: Article, variant: InputVariant) -> Article:
    """送給模型的那一份：main-only 只有 title 和 Main component，tags 與其餘 Component 都不送。
    送出的內容、注入的 Term List 條目、check 預期收到的 Component，都以這一份為準。"""
    if variant is InputVariant.FULL:
        return article
    if variant is InputVariant.MAIN_ONLY:
        return article.model_copy(update={"tags": [], "components": [c for c in article.components if c.type in MAIN_TYPES]})
    raise ValueError(f"沒有處理的輸入版本：{variant}")   # 新增輸入版本時，這裡要跟著加


def request_payload(article: Article) -> dict:
    """整篇 JSON 送入的內容：只含 title、tags、components，不送 meta（來源網址等）與頻道。"""
    return {"title": article.title, "tags": article.tags, "components": [c.model_dump(mode="json") for c in article.components]}


def expected_components(article: Article) -> list[ExpectedComponent]:
    out = [ExpectedComponent(TITLE_ID, TITLE_TYPE)]
    out += [ExpectedComponent(c.id, c.type) for c in article.components]
    if article.tags:
        out.append(ExpectedComponent(TAGS_ID, TAGS_TYPE))
    return out
