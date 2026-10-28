"""HK01 公開文章頁 → 本專案的 Article 格式（與 CMS 匯出的 user turn 同一結構）。

只用標準庫。這裡不做網絡存取，只做解析與轉換，方便用合成資料測試。
"""
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

ALLOWED_HOSTS = {"www.hk01.com", "global.hk01.com"}
HEADING_TOKENS = {"h2": "h2", "h3": "h3", "h4": "h4"}
# 行內 token：content 就是可見文字，直接串接（link 的網址不保留）
INLINE_TOKENS = {"text", "boldText", "link", "boldLink"}
# 已知 block 型別（轉成 Component）。遇到其他型別不丟棄，而是記錄在 meta.unknown，等人檢視。
MAIN_TYPES = {"highlight", "paragraph", "heading", "quote"}

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_ARTICLE_URL = re.compile(r"^/([^/]+)/(\d+)(?:/|$)")
# 這些路徑長得像 /xxx/{數字}/...，但是頻道、標籤等列表頁，不是文章。
# 不能用 id 大小判斷：舊文章的 id 也很小（例如 99674）。
NON_ARTICLE_PREFIXES = {"channel", "zone", "tag", "tags", "search", "author", "issue", "topic", "video"}


class ParseError(ValueError):
    pass


def check_url(url):
    """只允許 https 的 HK01 網站，避免被引導去抓別的地方。"""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"不允許的網址：{url[:80]}")
    return url


def encode_url(url):
    """把網址裡的非 ASCII 字元做 percent-encode（sitemap 的網址是未編碼的中文）。已編碼的保持不變。"""
    check_url(url)
    parts = urlsplit(url)
    safe = "/%:@!$&'()*+,;=-._~"
    return parts._replace(path=quote(parts.path, safe=safe), query=quote(parts.query, safe=safe + "?")).geturl()


def article_id_from_url(url):
    m = _ARTICLE_URL.match(urlsplit(url).path)
    if not m or m.group(1) in NON_ARTICLE_PREFIXES:
        raise ValueError(f"不是文章網址：{url[:80]}")
    return int(m.group(2))


def extract_article(page_html):
    m = _NEXT_DATA.search(page_html)
    if not m:
        raise ParseError("找不到 __NEXT_DATA__")
    try:
        return json.loads(m.group(1))["props"]["initialProps"]["pageProps"]["article"]
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise ParseError(f"__NEXT_DATA__ 結構不符：{e!r}")


def _clean(text, meta):
    if "<" in text:
        meta["warnings"].append("token 內容含 '<'，已保留原樣")
    return html.unescape(text)


def _group_text(group, meta):
    """一個群組 → (文字, 標題層級或 None)。"""
    if isinstance(group, dict):  # 容錯：群組被攤平成單一 token
        group = [group]
    parts, level = [], None
    for tok in group:
        tt, content = tok.get("type"), tok.get("content")
        if tt in HEADING_TOKENS:
            level = HEADING_TOKENS[tt]
        elif tt not in INLINE_TOKENS:
            # 不認得的行內型別：保留文字（不丟失原文），並記錄供人檢視
            meta["unknown"].append(f"token:{tt}")
        if isinstance(content, str):
            parts.append(content)
    return _clean("".join(parts), meta), level


def _tokens_text(node, meta):
    """含 htmlTokens 的節點（faq 的問或答、table 的儲存格）→ 各群組以換行串接的文字。"""
    groups = (node or {}).get("htmlTokens", [])
    return "\n".join(t for t in (_group_text(g, meta)[0] for g in groups) if t)


def _convert_table(t, meta):
    """table → {name, description, caption, rows}。合併儲存格（colSpan/rowSpan）不保留。"""
    caption, rows = "", []
    for node in t.get("children", []):
        kind = node.get("type")
        if kind == "caption":
            caption = _tokens_text(node, meta)
        elif kind == "colgroup":
            continue
        elif kind == "row":
            row = []
            for cell in node.get("children", []):
                if cell.get("type") in ("td", "th"):
                    row.append(_tokens_text(cell, meta))
                else:
                    meta["unknown"].append(f"table-node:{cell.get('type')}")
            rows.append(row)
        else:
            meta["unknown"].append(f"table-node:{kind}")
    return {"name": _clean(t.get("name") or "", meta), "description": _clean(t.get("description") or "", meta), "caption": caption, "rows": rows}


def convert_article(art, url, fetched_at):
    """把 __NEXT_DATA__ 的 article 轉成 {title, mainChannel, tags, components, meta}。"""
    article_id = art["articleId"]
    meta = {
        "source": "public-web",
        "url": url,
        "articleId": article_id,
        "publishTime": art.get("publishTime"),
        "lastModifyTime": art.get("lastModifyTime"),
        "fetchedAt": fetched_at,
        "unknown": [],
        "warnings": [],
    }
    components = []

    def add(**kw):
        components.append({"id": f"{article_id}-{len(components) + 1:03d}", **kw})

    for block in art.get("blocks", []):
        kind = block.get("blockType")
        if kind == "summary":
            for s in block.get("summary", []):
                add(type="highlight", text=_clean(s, meta))
        elif kind == "text":
            # htmlTokens 是「群組」的陣列；一組 = 一個段落或小標題，組內是行內 token。
            for group in block.get("htmlTokens", []):
                text, level = _group_text(group, meta)
                if not text:
                    continue
                if level:
                    add(type="heading", level=level, text=text)
                else:
                    add(type="paragraph", text=text)
        elif kind == "code":
            add(type="code", code=block.get("htmlString") or "")  # 嵌入物，原樣保留，不當文字處理
        elif kind in ("video", "brightcove"):
            caption = block.get("caption")
            add(type="video", **({"caption": _clean(caption, meta)} if isinstance(caption, str) and caption else {}))
        elif kind == "faq":
            add(type="faq", items=[
                {"question": _tokens_text(qa.get("question"), meta), "answer": _tokens_text(qa.get("answer"), meta)}
                for qa in block.get("questionsAndAnswers", [])
            ])
        elif kind == "table":
            add(type="table", **_convert_table(block.get("table") or {}, meta))
        elif kind in ("image", "motiongraphic"):  # motiongraphic 是動態圖，結構與 image 相同，只有說明文字
            image = block.get("image") or {}
            add(type="image", caption=_clean(image.get("caption", ""), meta))
        elif kind == "quote":
            add(type="quote", text=_clean(block.get("message") or "", meta), source=_clean(block.get("author") or "", meta))
        elif kind == "gallery":
            add(type="gallery", captions=[_clean(i.get("caption", ""), meta) for i in block.get("images", [])])
        elif kind == "related":
            titles = [a["data"]["title"] for a in block.get("articles", []) if isinstance(a, dict) and "data" in a]
            add(type="related-article", titles=titles)
        else:
            meta["unknown"].append(f"block:{kind}")

    return {
        "title": art.get("title", ""),
        "mainChannel": art.get("mainCategory", ""),
        "tags": [t["tagName"] for t in art.get("tags", []) if "tagName" in t],
        "components": components,
        "meta": meta,
    }


def parse_page(page_html, url, fetched_at):
    return convert_article(extract_article(page_html), url, fetched_at)


def main_chars(article):
    """Main component 的字數（含標題），與 preview.html 的算法一致。"""
    n = len(article.get("title", ""))
    for c in article["components"]:
        if c["type"] in MAIN_TYPES:
            n += len(c.get("text", ""))
    return n


def length_bucket(chars):
    for limit, name in ((1000, "短"), (3000, "中"), (5000, "長")):
        if chars < limit:
            return name
    return "超長"


class _GlobalParser(HTMLParser):
    """從簡體版（伺服器渲染的 HTML）取出 page-article 內的 h2/h3/h4/p 文字。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0          # 在 page-article 內的巢狀深度；0 表示不在內
        self.in_block = None
        self.buf = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if self.depth == 0 and "page-article" in cls.split():
            self.depth = 1
            return
        if self.depth:
            if tag not in ("br", "img", "hr", "meta", "link", "input"):
                self.depth += 1
            if tag in ("p", "h2", "h3", "h4") and self.in_block is None:
                self.in_block, self.buf = tag, []

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag == self.in_block:
            text = "".join(self.buf).strip()
            if text:
                self.items.append((self.in_block, text))
            self.in_block = None
        self.depth -= 1

    def handle_data(self, data):
        if self.in_block is not None:
            self.buf.append(data)


def parse_global_page(page_html):
    """回傳 [(tag, text), ...]。只供比對用，不保證與繁體版 Component 一一對應。"""
    p = _GlobalParser()
    p.feed(page_html)
    if not p.items:
        raise ParseError("簡體頁沒有找到 page-article 內的文字")
    return p.items
