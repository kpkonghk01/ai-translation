"""screen_candidates 的測試（只用合成資料）。用法：python3 -I tools/test_screen_candidates.py"""
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screen_candidates as S  # noqa: E402


DAY = 86400
NOW = 1_800_000_000


def art(aid, title, chars=1000, topic="sexual", section="社會新聞", texts=(), related=(), has_global=False, published=0):
    return {"id": str(aid), "title": title, "chars": chars, "bucket": S.bucket(chars), "topic": topic, "section": section,
            "texts": list(texts), "related": list(related), "has_global": has_global, "published": published}


class ExcludeTests(unittest.TestCase):
    def test_known_false_positives_are_excluded_with_reason(self):
        for title in ("虛構｜退休教師沉迷射擊遊戲　練成神槍手", "虛構｜食品廠起火　刺鼻氣味飄散全區", "虛構｜部門公布上季交通意外傷亡數字", "某劇集殺青花絮",
                      "虛構｜某校增設女性專用自習室　稱與防性騷擾無關"):
            self.assertIsNotNone(S.exclusion_reason(title), title)

    def test_real_crime_titles_are_not_excluded(self):
        for title in ("某男涉強姦　判囚", "持刀襲擊途人　一人重傷", "槍擊案　多人受傷", "涉劫殺女子　士兵遭起訴"):
            self.assertIsNone(S.exclusion_reason(title), title)


class KeywordTests(unittest.TestCase):
    def test_has_sensitive_keyword(self):
        self.assertTrue(S.has_sensitive_keyword("男子涉強姦"))
        self.assertTrue(S.has_sensitive_keyword("持刀襲擊"))
        self.assertFalse(S.has_sensitive_keyword("茶餐廳推出新品"))

    def test_broader_keywords_cover_conflict_and_sexual_and_bullying_titles(self):
        for title in ("某國大規模空襲邊境城市", "某軍被指發射導彈", "士兵向人群開火", "男子與未成年人性交", "被告要求兒童口交",
                      "亂倫案開審", "酒吧命案", "鐵錘傷人案", "學生遭掌摑腳踢", "校園欺凌事件", "家暴受害者求助"):
            self.assertTrue(S.has_sensitive_keyword(title), title)

    def test_ordinary_titles_still_pass_as_non_sensitive(self):
        for title in ("底妝推介", "馬拉松下月開跑", "飛機座位怎樣揀", "麵店結業"):
            self.assertFalse(S.has_sensitive_keyword(title), title)


class CaseKeyTests(unittest.TestCase):
    def test_case_numbers_are_normalised(self):
        self.assertEqual(S.case_numbers("案件(HCCC101/2030)今日判刑"), {"HCCC101/2030"})
        self.assertEqual(S.case_numbers("HCCC 202/2030 及 DCCC303/2031"), {"HCCC202/2030", "DCCC303/2031"})
        self.assertEqual(S.case_numbers("普通文字 2025/10"), set())

    def test_series_prefix(self):
        self.assertEqual(S.series_prefix("某甲案｜控方傳召證人"), "某甲案")
        self.assertEqual(S.series_prefix("某乙案│行兇被告"), "某乙案")
        self.assertIsNone(S.series_prefix("有片｜街頭表演"))   # 不以「案」結尾
        self.assertIsNone(S.series_prefix("沒有分隔符號的標題案"))
        self.assertIsNone(S.series_prefix("案｜太短"))

    def test_series_prefix_ignores_sub_series_after_a_dot(self):
        self.assertEqual(S.series_prefix("某甲案．開案｜賣樓起衝突"), "某甲案")
        self.assertEqual(S.series_prefix("某甲案·結案陳詞｜控方"), "某甲案")
        self.assertEqual(S.series_prefix("某甲案．開案｜甲"), S.series_prefix("某甲案｜乙"))


class MaybeSameTests(unittest.TestCase):
    def test_long_common_substring_is_flagged_but_not_merged(self):
        a = [art(1, "甲國首都禮拜場所遇炸彈襲擊多人死"), art(2, "乙國禮拜場所遇炸彈襲擊至少數十人死"), art(3, "完全無關的另一件事情")]
        self.assertEqual(S.maybe_same(a, min_common=5)["1"], ["2"])
        self.assertEqual(S.maybe_same(a, min_common=5)["3"], [])
        self.assertEqual(len(S.group_cases(a)), 3)   # 只是提示，不改變分組

    def test_short_common_words_do_not_trigger(self):
        a = [art(1, "男子涉強姦被判囚"), art(2, "婦人涉虐兒被判囚")]
        self.assertEqual(S.maybe_same(a, min_common=5), {"1": [], "2": []})


class GroupTests(unittest.TestCase):
    def groups(self, arts):
        g = S.group_cases(arts)
        return {frozenset(m) for m in g.values()}

    def test_same_case_number_merges(self):
        a = [art(1, "甲甲甲甲", texts=["HCCC101/2030"]), art(2, "乙乙乙乙乙", texts=["案件（HCCC 101/2030）"]), art(3, "丙丙丙丙")]
        self.assertIn(frozenset({"1", "2"}), self.groups(a))
        self.assertIn(frozenset({"3"}), self.groups(a))

    def test_same_series_prefix_merges(self):
        a = [art(1, "某甲案｜甲甲甲"), art(2, "某甲案｜乙乙乙乙乙"), art(3, "另一案｜丙丙丙")]
        self.assertEqual(self.groups(a), {frozenset({"1", "2"}), frozenset({"3"})})

    def test_similar_titles_merge(self):
        a = [art(1, "某甲性侵多名少女　認罪囚十年"), art(2, "某甲性侵多名少女　其中一人自殺　認罪囚十年")]
        self.assertEqual(self.groups(a), {frozenset({"1", "2"})})

    def test_related_link_alone_does_not_merge_different_cases(self):
        # 相關文章列表常把「同類」而非「同案」的文章放在一起
        a = [art(1, "某甲長期侵犯家人　法官明言重判", related=["某乙向親屬施藥圖強姦　承認控罪"]),
             art(2, "某乙向親屬施藥圖強姦　承認控罪")]
        self.assertEqual(self.groups(a), {frozenset({"1"}), frozenset({"2"})})

    def test_related_link_plus_some_title_overlap_merges(self):
        a = [art(1, "某議員涉非法性交　拍拖只吃米線", related=["某議員涉非法性交　否認指控"]),
             art(2, "某議員涉非法性交　否認指控")]
        self.assertEqual(self.groups(a), {frozenset({"1", "2"})})

    def test_unrelated_titles_stay_apart(self):
        self.assertEqual(self.groups([art(1, "甲乙丙丁戊己"), art(2, "庚辛壬癸子丑")]), {frozenset({"1"}), frozenset({"2"})})


class DecisionTests(unittest.TestCase):
    def decide(self, arts, cms_cases=None, controls=2, **kw):
        return {r["articleId"]: r for r in S.screen(arts, cms_case_numbers=cms_cases or {}, controls=controls, now=NOW, **kw)}

    def test_excluded_and_review(self):
        a = [art(1, "虛構｜部門公布上季交通意外傷亡數字", topic="violence"),
             art(2, "旅遊好去處推介", topic="longform"),
             art(3, "男子涉強姦　被判囚", topic="sexual")]
        d = self.decide(a)
        self.assertEqual(d["1"]["decision"], "exclude")
        self.assertEqual(d["2"]["decision"], "review")      # 長文類沒有敏感關鍵字：交人看
        self.assertEqual(d["3"]["decision"], "keep")

    def test_max_two_per_case_prefers_longer_and_marks_the_rest(self):
        a = [art(i, f"殺人案｜第{i}篇", chars=c, topic="violence") for i, c in ((1, 500), (2, 2400), (3, 1500), (4, 900))]
        d = self.decide(a)
        self.assertEqual({k for k, v in d.items() if v["decision"] == "keep"}, {"2", "3"})
        self.assertEqual({k for k, v in d.items() if v["decision"] == "drop-same-case"}, {"1", "4"})
        self.assertEqual(len({v["group"] for v in d.values()}), 1)

    def test_cms_articles_use_up_the_quota(self):
        a = [art(1, "強姦案件甲　HCCC101/2030", chars=900, texts=["HCCC101/2030"]), art(2, "強姦案件甲續　HCCC101/2030", chars=800, texts=["HCCC101/2030"]), art(3, "斬人事件另一宗", chars=700)]
        d = self.decide(a, cms_cases={"HCCC101/2030": 2})
        self.assertEqual(d["1"]["decision"], "drop-same-case")
        self.assertEqual(d["2"]["decision"], "drop-same-case")
        self.assertIn("CMS", d["1"]["reason"])
        self.assertEqual(d["3"]["decision"], "keep")

    def test_cms_with_one_article_leaves_one_slot(self):
        a = [art(1, "強姦案件甲　HCCC101/2030", chars=900, texts=["HCCC101/2030"]), art(2, "強姦案件甲續　HCCC101/2030", chars=800, texts=["HCCC101/2030"])]
        d = self.decide(a, cms_cases={"HCCC101/2030": 1})
        self.assertEqual([k for k, v in d.items() if v["decision"] == "keep"], ["1"])

    def test_controls_are_limited_and_prefer_long_and_distinct_sections(self):
        a = [art(1, "普通甲", chars=2500, topic="control", section="地產樓市"), art(2, "普通乙", chars=2400, topic="control", section="地產樓市"),
             art(3, "普通丙", chars=1800, topic="control", section="旅遊"), art(4, "普通丁", chars=300, topic="control", section="旅遊")]
        d = self.decide(a, controls=2)
        self.assertEqual({k for k, v in d.items() if v["decision"] == "keep"}, {"1", "3"})   # 兩個不同頻道、較長者
        self.assertTrue(all(v["decision"] == "control-pool" for k, v in d.items() if k in {"2", "4"}))

    def test_maybe_same_column_lists_other_groups_only(self):
        a = [art(1, "甲國首都禮拜場所遇炸彈襲擊多人死", topic="conflict"), art(2, "乙國禮拜場所遇炸彈襲擊至少數十人死", topic="conflict"),
             art(3, "強姦案｜第一篇", topic="sexual"), art(4, "強姦案｜第二篇", topic="sexual")]
        d = self.decide(a)
        self.assertEqual(d["1"]["maybeSame"], "2")
        self.assertEqual(d["3"]["maybeSame"], "")    # 3、4 已是同一組，不重覆提示

    def test_control_with_sensitive_keyword_is_not_kept_as_control(self):
        d = self.decide([art(1, "誤入對照的強姦新聞", topic="control")], controls=5)
        self.assertEqual(d["1"]["decision"], "review")

    def test_review_min_chars_keeps_long_review_items_and_drops_short_ones(self):
        a = [art(1, "旅遊好去處推介", chars=1500, topic="longform"), art(2, "數碼產品新發佈", chars=600, topic="conflict")]
        d = self.decide(a, controls=0, review_min_chars=1000)
        self.assertEqual(d["1"]["decision"], "keep")
        self.assertIn("review", d["1"]["reason"])           # 保留原因要說明它原本是 review
        self.assertEqual(d["2"]["decision"], "drop-short-review")
        self.assertIn("1000", d["2"]["reason"])

    def test_review_min_chars_none_leaves_review_untouched(self):
        d = self.decide([art(1, "旅遊好去處推介", chars=600, topic="longform")], controls=0)
        self.assertEqual(d["1"]["decision"], "review")

    def test_long_review_items_still_obey_the_per_case_cap(self):
        a = [art(i, f"旅遊好去處推介｜第{i}篇", chars=1000 + i, topic="longform") for i in (1, 2, 3)]
        d = self.decide(a, controls=0, review_min_chars=1000)
        self.assertEqual(sorted(k for k, v in d.items() if v["decision"] == "keep"), ["2", "3"])
        self.assertEqual(d["1"]["decision"], "drop-same-case")

    def test_sensitive_looking_control_is_dropped_when_short_and_stays_review_when_long(self):
        a = [art(1, "誤入對照的強姦新聞甲", chars=300, topic="control"), art(2, "誤入對照的強姦新聞乙丙丁", chars=1500, topic="control")]
        d = self.decide(a, controls=5, review_min_chars=1000)
        self.assertEqual(d["1"]["decision"], "drop-short-review")
        self.assertEqual(d["2"]["decision"], "review")

    def test_macro_topic(self):
        self.assertEqual(S.macro_topic({"topic": "sexual", "title": "甲"}), "sexual")
        self.assertEqual(S.macro_topic({"topic": "longform", "title": "涉強姦案的深度報道"}), "sexual")   # 題材標籤不準時按標題
        self.assertEqual(S.macro_topic({"topic": "conflict", "title": "空襲"}), "conflict")
        for t in ("violence", "violence-hk", "longform"):
            self.assertEqual(S.macro_topic({"topic": t, "title": "持刀襲擊"}), "violence")

    def test_batch1_takes_all_long_articles_then_fills_to_sexual_half_and_splits_the_rest(self):
        a = [art(1, "強姦長文甲", chars=3500, topic="sexual"), art(2, "槍擊長文乙", chars=3200, topic="conflict")]
        # 各篇標題的尾巴互不相同，避免被當成同一案
        a += [art(10 + i, f"男子涉強姦{tail}", chars=1500 - i, topic="sexual") for i, tail in enumerate(["春夏秋冬", "金木水火", "東南西北", "天地玄黃", "宇宙洪荒", "日月盈昃"])]
        a += [art(30 + i, f"持刀襲擊{tail}", chars=1400 - i, topic="violence-hk") for i, tail in enumerate(["辰宿列張", "寒來暑往", "閏餘成歲", "律呂調陽", "雲騰致雨", "露結為霜"])]
        rows = S.screen(a, cms_case_numbers={}, controls=0, batch1_size=6, now=NOW)
        b1 = [r for r in rows if r["batch1"]]
        self.assertEqual(len(b1), 6)
        self.assertTrue({"1", "2"} <= {r["articleId"] for r in b1})                        # 長文先入選
        self.assertEqual(Counter(S.macro_topic(r) for r in b1), {"sexual": 3, "violence": 2, "conflict": 1})

    def test_batch1_spills_over_when_a_topic_runs_out(self):
        a = [art(1, "男子涉強姦春夏秋冬", topic="sexual")] + [art(10 + i, f"持刀襲擊{t}", topic="violence-hk") for i, t in enumerate(["辰宿列張", "寒來暑往", "閏餘成歲", "律呂調陽"])]
        rows = S.screen(a, cms_case_numbers={}, controls=0, batch1_size=4, now=NOW)
        self.assertEqual(sum(r["batch1"] for r in rows), 4)

    def test_batch1_prefers_recent_articles_within_a_topic(self):
        a = [art(1, "男子涉強姦春夏秋冬", chars=2000, topic="sexual", published=NOW - 200 * DAY),
             art(2, "男子涉強姦金木水火", chars=1000, topic="sexual", published=NOW - 10 * DAY)]
        rows = S.screen(a, cms_case_numbers={}, controls=0, batch1_size=1, now=NOW)
        self.assertEqual([r["articleId"] for r in rows if r["batch1"]], ["2"])      # 近期優先於字數

    def test_batch1_among_equally_recent_prefers_more_characters(self):
        a = [art(1, "男子涉強姦春夏秋冬", chars=1000, topic="sexual", published=NOW - 10 * DAY),
             art(2, "男子涉強姦金木水火", chars=2000, topic="sexual", published=NOW - 20 * DAY)]
        rows = S.screen(a, cms_case_numbers={}, controls=0, batch1_size=1, now=NOW)
        self.assertEqual([r["articleId"] for r in rows if r["batch1"]], ["2"])


if __name__ == "__main__":
    unittest.main()
