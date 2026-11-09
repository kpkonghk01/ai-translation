"""一個 Run 要送出哪些請求（unit），以及收到的結果怎樣組回譯文。

article-json：整篇一個請求；Approach 設了 max_input_chars 就按 Component 順序切成連續的塊，每塊一個請求。
  title 和 tags 只放在第一塊，其他塊送空的。一個 Component 超過上限時單獨一塊，不拆也不丟。
per-component：每個要翻譯的字串一個請求（gallery 的每則說明、quote 的出處也各一個），送純文字、收純文字，
  給不擅長處理 JSON 的翻譯專用模型用。不翻譯的 Component 和空白的字串不送，原樣放回去。
兩種模式組出來的譯文結構相同（Component id 不變），Run 紀錄的格式也相同。
"""
import json
from dataclasses import dataclass

from .articles import article_text, request_payload, text_field_names
from .schemas import ApproachConfig, Article


@dataclass(frozen=True)
class Unit:
    label: str | None   # 只有一個請求的 Run 沒有標籤
    user_text: str
    source_text: str    # 這個請求裡的原文：注入 Term List 條目用
    where: tuple = ()   # per-component：這段文字在譯文裡的位置


def _json_text(title: str, tags: list, components: list) -> str:
    return json.dumps({"title": title, "tags": tags, "components": components}, ensure_ascii=False)


def _chunks(article: Article, limit: int) -> list[list[dict]]:
    """按 Component 順序貪心地切：加進去會超過上限就換下一塊。第一塊的大小包括 title 和 tags。"""
    components = request_payload(article)["components"]
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for component in components:
        first = not chunks
        size = len(_json_text(article.title if first else "", article.tags if first else [], current + [component]))
        if current and size > limit:
            chunks.append(current)
            current = [component]
        else:
            current.append(component)
    chunks.append(current)
    return chunks


def plan_units(article: Article, approach: ApproachConfig) -> list[Unit]:
    if approach.request_mode == "per-component":
        return _component_units(article)
    if approach.max_input_chars is None:
        return [Unit(None, _json_text(article.title, article.tags, request_payload(article)["components"]), article_text(article))]
    chunks = _chunks(article, approach.max_input_chars)
    by_id = {c.id: c for c in article.components}
    units = []
    for number, chunk in enumerate(chunks, 1):
        first = number == 1
        title, tags = (article.title, article.tags) if first else ("", [])
        part = article.model_copy(update={"title": title, "tags": tags, "components": [by_id[c["id"]] for c in chunk]})
        units.append(Unit(f"chunk {number}/{len(chunks)}" if len(chunks) > 1 else None, _json_text(title, tags, chunk), article_text(part)))
    return units


def _component_units(article: Article) -> list[Unit]:
    units = []
    if article.title.strip():
        units.append(Unit("title", article.title, article.title, ("title",)))
    for index, tag in enumerate(article.tags):
        if tag.strip():
            units.append(Unit(f"tags[{index + 1}]", tag, tag, ("tags", index)))
    for component in article.components:
        fields = component.model_dump(mode="json")
        for name in text_field_names(component.type):
            value = fields.get(name)
            if isinstance(value, str) and value.strip():
                units.append(Unit(f"{component.id}.{name}", value, value, ("component", component.id, name, None)))
            elif isinstance(value, list):
                for position, item in enumerate(value):
                    if isinstance(item, str) and item.strip():
                        units.append(Unit(f"{component.id}.{name}[{position + 1}]", item, item, ("component", component.id, name, position)))
    return units


def merge_chunks(payloads: list[dict]) -> dict:
    """切塊的譯文組回一份：title 與 tags 取第一塊的，Component 依序接起來。"""
    merged = {k: payloads[0][k] for k in ("title", "tags") if k in payloads[0]}
    merged["components"] = [c for payload in payloads for c in payload.get("components", [])]
    return merged


def merge_components(article: Article, units: list[Unit], texts: list[str]) -> dict:
    """逐 Component 的譯文放回原文的結構：id、type 和其他欄位（例如 level）不變，沒有送出的字串與不翻譯的 Component 原樣。"""
    translated = {unit.where: text for unit, text in zip(units, texts)}
    components = []
    for component in article.components:
        fields = component.model_dump(mode="json")
        for name in text_field_names(component.type):
            value = fields.get(name)
            if isinstance(value, str):
                fields[name] = translated.get(("component", component.id, name, None), value)
            elif isinstance(value, list):
                fields[name] = [translated.get(("component", component.id, name, position), item) for position, item in enumerate(value)]
        components.append(fields)
    return {"title": translated.get(("title",), article.title),
            "tags": [translated.get(("tags", index), tag) for index, tag in enumerate(article.tags)],
            "components": components}
