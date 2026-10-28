"""把一篇文章印成「id｜type｜文字」的精簡格式，方便閱讀與草擬 ground truth。

用法：python3 -I tools/dump_article.py data/web-articles/60402519.json [...]

只印有文字的 component（text / caption / titles / source）；code、video 只印 type。
"""
import json
import sys


def lines(article):
    yield f"# {article['title']}（{article.get('mainChannel', '')}）"
    for c in article["components"]:
        t = c["type"]
        if t in ("code", "video"):
            yield f"{c['id']}｜{t}"
        elif t == "table":
            yield f"{c['id']}｜table｜" + " / ".join("、".join(r) for r in c.get("rows", []))
        elif t == "faq":
            yield f"{c['id']}｜faq｜" + " / ".join(f"{q['question']}→{q['answer']}" for q in c.get("items", []))
        elif t == "gallery":
            yield f"{c['id']}｜gallery｜" + " / ".join(dict.fromkeys(c.get("captions", [])))
        elif t == "related-article":
            yield f"{c['id']}｜related｜" + " / ".join(c.get("titles", []))
        elif t == "image":
            yield f"{c['id']}｜image｜{c.get('caption', '')}"
        else:
            text = c.get("text", "")
            yield f"{c['id']}｜{t}｜{text}" + (f"（{c['source']}）" if c.get("source") else "")


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as f:
            print("\n".join(lines(json.load(f))), end="\n\n")
