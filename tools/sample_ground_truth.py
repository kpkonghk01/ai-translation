"""從 ground truth 草稿抽出一份給人審閱的樣本（固定種子，可重現），並統計 item 與「不重覆的句子」數。

樣本 = 全部 NOT-NEGATIVE? 的文章 + 有 item 的對照文章 + 敏感文章中隨機抽 general / political 各若干篇。

用法：
  python3 -I tools/sample_ground_truth.py data/gt-drafts/draft-{a,b,c,d}.json --general 5 --political 5 --seed 1 --out data/gt-drafts/review-sample.json
之後用 render_ground_truth.py 把這份樣本轉成審閱用的 Markdown。
"""
import argparse
import json
import random
import sys


def _all_articles(gts):
    return [a for gt in gts for a in gt["articles"]]


def summarize(gts):
    arts = _all_articles(gts)
    items = [(a["article_id"], it["component_id"]) for a in arts for it in a["items"]]
    return {
        "articles": len(arts),
        "items": len(items),
        "distinct_components": len(set(items)),   # 同一個長段落可能被拆成多個 item，統計獨立句子數要用這個
        "facts": sum(len(it["must_retain"]) for a in arts for it in a["items"]),
    }


def sample(gts, general=5, political=5, seed=1):
    arts = _all_articles(gts)
    flagged = [a for a in arts if a["note"].startswith("NOT-NEGATIVE")]
    controls = [a for a in arts if a["role"] == "control" and a["items"]]
    flagged_ids = {a["article_id"] for a in flagged}
    pool = [a for a in arts if a["role"] == "sensitive" and a["items"] and a["article_id"] not in flagged_ids]
    general_pool = sorted((a for a in pool if a.get("domain", "general") == "general"), key=lambda a: a["article_id"])
    political_pool = sorted((a for a in pool if a.get("domain") == "china-political"), key=lambda a: a["article_id"])
    rng = random.Random(seed)
    chosen = flagged + controls
    chosen += rng.sample(general_pool, min(general, len(general_pool)))
    chosen += rng.sample(political_pool, min(political, len(political_pool)))
    return {"articles": sorted(chosen, key=lambda a: a["article_id"])}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("gt", nargs="+")
    p.add_argument("--general", type=int, default=5)
    p.add_argument("--political", type=int, default=5)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    gts = []
    for path in a.gt:
        with open(path, encoding="utf-8") as f:
            gts.append(json.load(f))
    out = sample(gts, a.general, a.political, a.seed)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    full, part = summarize(gts), summarize([out])
    print(f"全部：{full['articles']} 篇、{full['items']} 個 item（不重覆句子 {full['distinct_components']}）、{full['facts']} 條事實")
    print(f"樣本：{part['articles']} 篇、{part['items']} 個 item、{part['facts']} 條事實 → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
