"""Deterministic check 的內容規則：Terminology 與繁→英殘留漢字（Finding，計分）。只用虛構文字。"""
import copy
import unittest

from content_cases import ContentCheckCase, build, paragraph
from fixtures import TERM_LIST

CITY_HALL = "Fictional City Hall"


class TerminologyTest(ContentCheckCase):
    def terminology(self, rows, component_id):
        return [f for f in rows[component_id]["findings"] if f["dimension"] == "Terminology"]

    def test_a_term_with_none_of_its_allowed_renderings_is_a_terminology_finding(self):
        source, translation = build([paragraph("市民陳虛構表示不滿。", "Resident Chen Fictitious voiced his anger.")])
        rows = self.check_pair(source, translation, TERM_LIST)
        [finding] = self.terminology(rows, "9000001-001")
        self.assertEqual((finding["subtype"], finding["severity"]), ("term-mismatch", "major"))   # 嚴重度取自條目
        self.assertEqual((finding["source_span"], finding["target_span"]), ("陳虛構", ""))   # 原文片段是那個詞，譯文片段留空
        self.assertEqual(finding["source"], "deterministic")

    def test_any_one_allowed_rendering_is_enough_ignoring_case_and_runs_of_spaces(self):
        for rendering in ("Resident Fictional Chan said so.", "RESIDENT  chan   FICTIONAL said so."):
            with self.subTest(rendering):
                source, translation = build([paragraph("市民陳虛構表示。", rendering)])
                rows = self.check_pair(source, translation, TERM_LIST)
                self.assertEqual(self.terminology(rows, "9000001-001"), [])

    def test_severity_defaults_to_minor_and_the_title_is_checked_too(self):
        source, translation = build([paragraph("沒有詞。", "Nothing.")], title="虛構市政廳的新聞", translated_title="News of the hall")
        rows = self.check_pair(source, translation, TERM_LIST)
        [finding] = self.terminology(rows, "title")
        self.assertEqual(finding["severity"], "minor")
        self.assertEqual(finding["source_span"], "虛構市政廳")

    def test_a_term_is_checked_in_the_component_that_contains_it(self):
        source, translation = build([
            paragraph("市民陳虛構表示。", "A resident said so."),            # 有原文詞，譯文沒有指定譯法
            paragraph("沒有任何詞。", "Fictional Chan appears elsewhere."),    # 別處的譯文出現了指定譯法，不能抵銷上面那個
        ])
        rows = self.check_pair(source, translation, TERM_LIST)
        self.assertEqual(len(self.terminology(rows, "9000001-001")), 1)
        self.assertEqual(self.terminology(rows, "9000001-002"), [])

    def test_one_finding_per_term_per_component_however_often_it_occurs(self):
        source, translation = build([paragraph("陳虛構說，陳虛構又說。", "He said, then said again.")])
        rows = self.check_pair(source, translation, TERM_LIST)
        self.assertEqual(len(self.terminology(rows, "9000001-001")), 1)

    def test_a_missing_component_gets_the_omission_but_no_terminology_finding(self):
        source, translation = build([paragraph("市民陳虛構表示。", "x")])
        translation["components"].clear()
        rows = self.check_pair(source, translation, TERM_LIST)
        self.assertEqual(self.findings(rows["9000001-001"]), [("Accuracy", "omission", "major")])

    def test_there_are_no_terminology_findings_without_a_term_list(self):
        source, translation = build([paragraph("市民陳虛構表示。", "Resident Chen Fictitious.")])
        rows = self.check_pair(source, translation)
        self.assertEqual(self.terminology(rows, "9000001-001"), [])

    def test_a_term_that_is_not_in_the_article_is_not_checked(self):
        only_absent = copy.deepcopy(TERM_LIST)
        only_absent["terms"] = [only_absent["terms"][2]]
        source, translation = build([paragraph("市民表示。", "A resident said so.")])
        rows = self.check_pair(source, translation, only_absent)
        self.assertEqual([cid for cid in rows if self.terminology(rows, cid)], [])


class ResidualChineseTest(ContentCheckCase):
    def residue(self, row):
        return [f["target_span"] for f in row["findings"] if f["subtype"] == "residual-han"]

    def test_each_run_of_chinese_characters_left_in_the_english_is_one_minor_locale_finding(self):
        source, translation = build([paragraph("甲乙丙。", "He met 虛構市政 and then 陳 at 虛構市政.")])
        rows = self.check_pair(source, translation)
        row = rows["9000001-001"]
        self.assertEqual(self.residue(row), ["虛構市政", "陳", "虛構市政"])   # 連續的漢字記一次；同一段出現兩次是兩段
        self.assertEqual({(f["dimension"], f["severity"], f["source"]) for f in row["findings"]}, {("Locale & Format", "minor", "deterministic")})

    def test_chinese_punctuation_is_not_a_character_to_flag(self):
        source, translation = build([paragraph("甲乙丙。", "He said，then left。「Quote」")])
        self.assertEqual(self.residue(self.check_pair(source, translation)["9000001-001"]), [])

    def test_the_title_tags_and_captions_are_checked_too(self):
        source, translation = build(
            [("gallery", {"captions": ["圖一", "圖二"]}, {"captions": ["Photo one", "圖二"]}),
             ("related-article", {"titles": ["相關"]}, {"titles": ["Related 相關"]})],
            translated_title="Title 標題", translated_tags=("Fictional", "標籤"))
        rows = self.check_pair(source, translation)
        self.assertEqual(self.residue(rows["title"]), ["標題"])
        self.assertEqual(self.residue(rows["tags"]), ["標籤"])
        self.assertEqual(self.residue(rows["9000001-001"]), ["圖二"])
        self.assertEqual(self.residue(rows["9000001-002"]), ["相關"])

    def test_untranslated_components_may_keep_their_chinese(self):
        source, translation = build([("code", {"code": "<p>虛構</p>"}, {"code": "<p>虛構</p>"})])
        self.assertEqual(self.residue(self.check_pair(source, translation)["9000001-001"]), [])

    def test_clean_english_has_no_findings(self):
        source, translation = build([paragraph("甲乙丙。", "Clean English text.")])
        self.assertEqual(self.check_pair(source, translation)["9000001-001"]["findings"], [])


if __name__ == "__main__":
    unittest.main()
