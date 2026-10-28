"""比對 global.hk01.com 簡體版與「OpenCC 轉換繁體版」的結果，判斷簡體版是否只是字形轉換。

用法（需要裝了 OpenCC 的環境）：
  .venv/bin/python -I tools/compare_global.py --trad data/web-articles --glob data/web-articles-global

--configs 可以是 OpenCC 內建名稱（hk2s、t2s、tw2sp），也可以是設定檔路徑；
要檢視「現行服務」就傳它的自訂設定檔（含自訂 dictionary）。

注意：這只是診斷工具。內建名稱是標準 OpenCC，不是公司現行服務，
結果不能當方案 E 的 baseline；baseline 必須由現行服務（自訂 dictionary）產生。

只輸出彙總數字與「繁體字 → 簡體字」的差異統計，不印整段文字。
"""
import argparse
import collections
import difflib
import json
import os
import re

TEXT_TYPES = ("highlight", "paragraph", "heading", "quote")
MIN_SIMILARITY = 0.6  # 低於此值視為簡體版沒有對應段落（多出、缺少或改寫）


def norm(s):
    return re.sub(r"\s+", "", s)


def trad_texts(article):
    return [norm(c["text"]) for c in article["components"] if c["type"] in TEXT_TYPES and norm(c.get("text", ""))]


def glob_texts(g):
    return [norm(t) for _, t in g["items"] if norm(t)]


def char_pairs(a, b):
    """a（轉換後）與 b（簡體版）之間，被替換的字元對。"""
    out = collections.Counter()
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "replace":
            out[(a[i1:i2], b[j1:j2])] += 1
    return out


def compare(trad, glob, convert):
    """trad / glob: 文字清單。回傳 dict：matched, total, no_counterpart, pairs。"""
    remaining = list(glob)
    matched, no_counterpart, pairs = 0, 0, collections.Counter()
    for t in trad:
        c = convert(t)
        if c in remaining:
            remaining.remove(c)
            matched += 1
            continue
        best, ratio = None, 0.0
        for g in remaining:
            r = difflib.SequenceMatcher(None, c, g, autojunk=False).ratio()
            if r > ratio:
                best, ratio = g, r
        if best is None or ratio < MIN_SIMILARITY:
            no_counterpart += 1
        else:
            pairs.update(char_pairs(c, best))
            remaining.remove(best)
    return {"matched": matched, "total": len(trad), "no_counterpart": no_counterpart, "pairs": pairs, "extra_in_global": len(remaining)}


def load_pairs(trad_dir, glob_dir):
    for name in sorted(os.listdir(trad_dir)):
        gpath = os.path.join(glob_dir, name)
        if name.endswith(".json") and os.path.exists(gpath):
            with open(os.path.join(trad_dir, name), encoding="utf-8") as f:
                t = json.load(f)
            with open(gpath, encoding="utf-8") as f:
                g = json.load(f)
            yield name[:-5], t, g


def main():
    import opencc  # 只在 CLI 需要

    p = argparse.ArgumentParser()
    p.add_argument("--trad", required=True)
    p.add_argument("--glob", required=True)
    p.add_argument("--configs", nargs="+", default=["hk2s", "t2s", "tw2sp"])
    p.add_argument("--top", type=int, default=15)
    a = p.parse_args()

    pairs = list(load_pairs(a.trad, a.glob))
    print(f"同時有繁體與簡體版的文章：{len(pairs)} 篇")
    for cfg in a.configs:
        conv = opencc.OpenCC(cfg).convert
        tot = collections.Counter()
        diffs = collections.Counter()
        per_article_perfect = 0
        for _, t, g in pairs:
            r = compare(trad_texts(t), glob_texts(g), lambda s: norm(conv(s)))
            tot.update({k: r[k] for k in ("matched", "total", "no_counterpart", "extra_in_global")})
            diffs.update(r["pairs"])
            per_article_perfect += r["matched"] == r["total"] and r["extra_in_global"] == 0
        print(f"\n[{cfg}] 段落完全吻合 {tot['matched']}/{tot['total']}；無對應 {tot['no_counterpart']}；簡體版多出 {tot['extra_in_global']}；整篇完全吻合 {per_article_perfect}/{len(pairs)}")
        for (x, y), n in diffs.most_common(a.top):
            print(f"   {x} → {y}  ×{n}")


if __name__ == "__main__":
    main()
