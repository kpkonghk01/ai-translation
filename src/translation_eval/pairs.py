"""一個 Run 的原文與譯文，按 Component 對齊：Judge 逐 Component 評核用。

title 與 tags 用保留的 Component id；gallery 的說明、related-article 的標題、tags 以換行連成一段文字。
不翻譯的 Component 不在內。譯文缺少的 Component 沒有譯文可以評（漏段由 check 記為 Finding），translated_text 是 None。
"""
from dataclasses import dataclass

from .articles import MAIN_TYPES, TAGS_ID, TAGS_TYPE, TITLE_ID, TITLE_TYPE, source_texts, text_of
from .schemas import Article, Translation


@dataclass(frozen=True)
class ComponentPair:
    id: str
    type: str
    source_text: str
    translated_text: str | None

    @property
    def is_main(self) -> bool:
        """Main component：title、highlight、heading、paragraph、quote；其餘（圖說、related-article 標題、tags）是 Peripheral。"""
        return self.type == TITLE_TYPE or self.type in MAIN_TYPES


def component_pairs(article: Article, translation: Translation) -> list[ComponentPair]:
    """依原文的順序。重覆的 id 取第一個；title 是空白、tags 是空的視同缺少。"""
    real = {}
    for c in translation.components:
        real.setdefault(c.id, c)
    pairs = []
    for component_id, source_text in source_texts(article).items():
        if source_text is None:
            continue
        if component_id == TITLE_ID:
            component_type, translated = TITLE_TYPE, text_of(TITLE_TYPE, {"title": translation.title}) if translation.title else None
        elif component_id == TAGS_ID:
            component_type, translated = TAGS_TYPE, text_of(TAGS_TYPE, {"tags": translation.tags}) if translation.tags else None
        else:
            source_component = next(c for c in article.components if c.id == component_id)
            component_type = source_component.type
            found = real.get(component_id)
            translated = text_of(component_type, found.model_dump()) if found is not None else None
        pairs.append(ComponentPair(component_id, component_type, source_text, translated.strip() if translated and translated.strip() else None))
    return pairs
