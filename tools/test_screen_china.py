"""screen_china 的測試（只用合成資料）。用法：python3 -I tools/test_screen_china.py"""
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screen_china as S  # noqa: E402

DAY = 86400
NOW = 1_800_000_000


def art(aid, title, topic="corruption", chars=1000, section="即時中國", days_old=30, texts=(), related=()):
    return {"id": str(aid), "title": title, "chars": chars, "bucket": "中", "topic": topic, "section": section,
            "texts": list(texts), "related": list(related), "has_global": False, "published": NOW - days_old * DAY}


def distinct(prefix, n):
    """產生互不相似的標題，避免被當成同一案。"""
    words = ["春夏秋冬", "金木水火", "東南西北", "天地玄黃", "宇宙洪荒", "日月盈昃", "辰宿列張", "寒來暑往", "閏餘成歲", "律呂調陽"]
    return [f"{prefix}{words[i]}" for i in range(n)]


class SelectTests(unittest.TestCase):
    def pick(self, arts, **kw):
        return {r["articleId"]: r for r in S.select(arts, now=NOW, **kw)}

    def selected(self, arts, **kw):
        return {k for k, v in self.pick(arts, **kw).items() if v["selected"]}

    def test_per_topic_quota(self):
        a = []
        for topic, prefix in (("corruption", "貪"), ("rights", "權")):
            a += [art(f"{topic}{i}", t, topic=topic) for i, t in enumerate(distinct(prefix, 5))]
        got = self.selected(a, per_topic=2, total=4)
        self.assertEqual(Counter(k.rstrip("0123456789") for k in got), {"corruption": 2, "rights": 2})

    def test_recent_beats_older_and_longer(self):
        a = [art(1, "官員涉貪春夏秋冬", chars=3000, days_old=900), art(2, "官員涉貪金木水火", chars=800, days_old=60)]
        self.assertEqual(self.selected(a, per_topic=1, total=1), {"2"})

    def test_news_channel_beats_analysis_channel_when_equally_recent(self):
        a = [art(1, "官員涉貪春夏秋冬", chars=3000, section="中國觀察"), art(2, "官員涉貪金木水火", chars=900, section="即時中國")]
        self.assertEqual(self.selected(a, per_topic=1, total=1), {"2"})

    def test_longer_wins_when_equally_recent_and_same_channel(self):
        a = [art(1, "官員涉貪春夏秋冬", chars=900), art(2, "官員涉貪金木水火", chars=1500)]
        self.assertEqual(self.selected(a, per_topic=1, total=1), {"2"})

    def test_too_short_articles_are_not_selected(self):
        a = [art(1, "官員涉貪春夏秋冬", chars=120), art(2, "官員涉貪金木水火", chars=900)]
        got = self.pick(a, per_topic=2, total=2, min_chars=300)
        self.assertEqual({k for k, v in got.items() if v["selected"]}, {"2"})
        self.assertEqual(got["1"]["reason"], "字數不足 300")

    def test_same_case_cap_applies_across_topics(self):
        a = [art(i, f"某甲事件｜第{i}篇", topic="accountability") for i in (1, 2, 3)] + [art(9, "某甲事件｜第九篇", topic="rights")]
        got = self.pick(a, per_topic=3, total=10, max_per_case=2)
        self.assertEqual(sum(v["selected"] for v in got.values()), 2)
        self.assertTrue(all("同案" in v["reason"] for v in got.values() if not v["selected"]))

    def test_shortfall_is_filled_from_other_topics_up_to_total(self):
        a = [art("c0", "貪春夏秋冬", topic="corruption")] + [art(f"r{i}", t, topic="rights") for i, t in enumerate(distinct("權", 6))]
        got = self.selected(a, per_topic=3, total=5)
        self.assertEqual(len(got), 5)
        self.assertIn("c0", got)

    def test_total_is_never_exceeded(self):
        a = [art(f"r{i}", t, topic="rights") for i, t in enumerate(distinct("權", 6))] + [art(f"c{i}", t, topic="corruption") for i, t in enumerate(distinct("貪", 6))]
        self.assertEqual(len(self.selected(a, per_topic=5, total=7)), 7)

    def test_rows_report_group_and_year(self):
        r = self.pick([art(1, "官員涉貪春夏秋冬", days_old=10)], per_topic=1, total=1)["1"]
        self.assertTrue(r["group"].startswith("G"))
        self.assertEqual(r["analysis"], False)
        self.assertEqual(S.select([art(2, "官員涉貪春夏秋冬", section="中國觀察")], now=NOW)[0]["analysis"], True)


if __name__ == "__main__":
    unittest.main()
