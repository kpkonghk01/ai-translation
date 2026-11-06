"""讀入文章檔，並決定送給模型的內容與譯文應有的 Component。

title 與 tags 沒有 id：評核時用保留的 Component id（TITLE_ID、TAGS_ID）與其他 Component 一樣對齊。
"""
import json
import os
from dataclasses import dataclass

from pydantic import ValidationError

from .errors import InputError
from .schemas import Article

TITLE_ID = "title"
TAGS_ID = "tags"


@dataclass(frozen=True)
class ExpectedComponent:
    id: str
    type: str


def load_article(directory: str, article_id: str) -> Article:
    path = os.path.join(directory, f"{article_id}.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as e:
        raise InputError(f"文章 {article_id}：讀不到 {path}（{type(e).__name__}）") from e
    except json.JSONDecodeError as e:
        raise InputError(f"文章 {article_id}：不是合法的 JSON（第 {e.lineno} 行）") from e
    try:
        article = Article.model_validate(raw)
    except ValidationError as e:
        raise InputError(f"文章 {article_id}：{'.'.join(str(p) for p in err['loc'])}：{err['msg']}" for err in e.errors()) from e
    ids = [c.id for c in article.components]
    duplicated = sorted({i for i in ids if ids.count(i) > 1} | ({TITLE_ID, TAGS_ID} & set(ids)))
    if duplicated:
        raise InputError(f"文章 {article_id}：Component id 重覆或與保留的 id 衝突：{', '.join(duplicated)}")
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
