"""從中國負面新聞候選（約 131 篇）建議約 30 篇。只是建議，最後由人決定。

規則（Pan 於 2026-10-28 同意，Q32）：
  - 每個子類各取 per_topic 篇（預設 3，共 10 個子類 = 30）；子類不足時由其他子類按同一排序補滿
  - 排序：近 RECENT_DAYS 天發佈者優先 → 新聞頻道優先於分析頻道 → 字數較多者優先
  - 同案最多 2 篇（跨子類計算）；字數不足 min_chars 的不收
  - 「對中國政府不利」要靠人看標題判斷，這裡不做自動判斷

用法：
  python3 -I tools/screen_china.py --web data/web-articles-china --glob data/web-articles-china-global --out data/china-screened.tsv
"""
import argparse
import os
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hk01_collect as C  # noqa: E402
import screen_candidates as S  # noqa: E402

RECENT_DAYS = 365
MIN_CHARS = 300
# 這些頻道偏分析或評論；不剔除，只在排序時排在即時新聞之後
ANALYSIS_SECTIONS = {"中國觀察", "國際分析", "政情", "深度報道", "01論壇", "世界專題"}


def select(arts, per_topic=3, total=30, max_per_case=2, min_chars=MIN_CHARS, recent_days=RECENT_DAYS, now=None):
    now = time.time() if now is None else now
    recent_after = now - recent_days * 86400

    def rank(a):
        return (a["published"] < recent_after, a["section"] in ANALYSIS_SECTIONS, -a["chars"], a["id"])

    groups = S.group_cases(arts)
    group_of = {i: g for g, ids in groups.items() for i in ids}
    reason, chosen, case_count = {}, [], Counter()
    eligible = []
    for a in arts:
        if a["chars"] < min_chars:
            reason[a["id"]] = f"字數不足 {min_chars}"
        else:
            eligible.append(a)

    def take(a):
        g = group_of[a["id"]]
        if case_count[g] >= max_per_case:
            reason[a["id"]] = f"同案名額已滿（上限 {max_per_case}）"
            return False
        case_count[g] += 1
        chosen.append(a)
        return True

    by_topic = defaultdict(list)
    for a in eligible:
        by_topic[a["topic"]].append(a)
    for topic in sorted(by_topic):
        got = 0
        for a in sorted(by_topic[topic], key=rank):
            if got >= per_topic or len(chosen) >= total:
                break
            got += take(a)
    taken = {a["id"] for a in chosen}
    for a in sorted((a for a in eligible if a["id"] not in taken), key=rank):   # 子類不足時補滿
        if len(chosen) >= total:
            break
        take(a)

    taken = {a["id"] for a in chosen}
    return [{
        "articleId": a["id"], "selected": a["id"] in taken, "topic": a["topic"], "section": a["section"],
        "year": time.gmtime(a["published"]).tm_year, "chars": a["chars"], "analysis": a["section"] in ANALYSIS_SECTIONS,
        "group": group_of[a["id"]], "reason": reason.get(a["id"], ""), "title": a["title"],
    } for a in sorted(arts, key=lambda a: (a["id"] not in taken, a["topic"], rank(a)))]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--web", required=True)
    p.add_argument("--glob", required=True)
    p.add_argument("--per-topic", type=int, default=3)
    p.add_argument("--total", type=int, default=30)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    rows = select(S.load_articles(a.web, a.glob), per_topic=a.per_topic, total=a.total)
    C.write_tsv(a.out, rows, ["articleId", "selected", "topic", "section", "year", "chars", "analysis", "group", "reason", "title"])
    sel = [r for r in rows if r["selected"]]
    print(f"{len(rows)} 篇 → {a.out}；建議 {len(sel)} 篇")
    print("子類:", dict(Counter(r["topic"] for r in sel)))
    print("年份:", dict(sorted(Counter(r["year"] for r in sel).items())), "| 分析類頻道:", sum(r["analysis"] for r in sel), "| 字數中位:", sorted(r["chars"] for r in sel)[len(sel) // 2])


if __name__ == "__main__":
    main()
