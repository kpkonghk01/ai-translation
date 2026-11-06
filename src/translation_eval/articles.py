"""讀入文章檔，並決定送給模型的內容與譯文應有的 Component。

title 與 tags 沒有 id：評核時用保留的 Component id（TITLE_ID、TAGS_ID）與其他 Component 一樣對齊。
"""
import os
from dataclasses import dataclass

from .errors import InputError
from .loading import read_model
from .schemas import Article

TITLE_ID = "title"
TAGS_ID = "tags"
MAX_LISTED_UNKNOWN = 5


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
    # 轉換器遇到不認識的東西會記在 meta.unknown：這樣的文章可能不完整，要先檢視，不當作完整的原文去翻譯
    meta = (article.model_extra or {}).get("meta")
    unknown = meta.get("unknown") if isinstance(meta, dict) else None
    if unknown:
        listed = ", ".join(str(u)[:40] for u in unknown[:MAX_LISTED_UNKNOWN])
        raise InputError(f"文章 {article_id}：轉換器在 meta.unknown 記了 {len(unknown)} 項（{listed}），先檢視再翻譯")
    return article


def request_payload(article: Article) -> dict:
    """整篇 JSON 送入的內容：只含 title、tags、components，不送 meta（來源網址等）與頻道。"""
    return {"title": article.title, "tags": article.tags, "components": [c.model_dump(mode="json") for c in article.components]}


def expected_components(article: Article) -> list[ExpectedComponent]:
    out = [ExpectedComponent(TITLE_ID, "title")]
    out += [ExpectedComponent(c.id, c.type) for c in article.components]
    if article.tags:
        out.append(ExpectedComponent(TAGS_ID, "tags"))
    return out
