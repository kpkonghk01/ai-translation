"""Deterministic check 的警示規則：只標出來交人手看，不計分（不產生 Finding）。只用虛構文字。"""
import shutil
import unittest

from content_cases import ContentCheckCase, build, paragraph

SOURCE_26_CHARS = "虛構市政廳翻新工程嚴重延誤" * 2


class WarningsDoNotScoreTest(ContentCheckCase):
    def test_a_warning_is_not_a_finding(self):
        source, translation = build([paragraph(SOURCE_26_CHARS, "OK.")])
        row = self.check_pair(source, translation)["9000001-001"]
        self.assertEqual(self.warnings(row), ["length-ratio"])
        self.assertEqual(row["findings"], [])

    def test_a_missing_component_gets_the_omission_only(self):
        source, translation = build([paragraph(SOURCE_26_CHARS, "x")])
        translation["components"].clear()
        row = self.check_pair(source, translation)["9000001-001"]
        self.assertEqual((self.findings(row), self.warnings(row)), ([("Accuracy", "omission", "major")], []))

    def test_a_faithful_translation_has_no_warnings(self):
        source, translation = build([paragraph("他說「甲」，涉款1,500萬元。", 'He said "A", accused of HK$15 million.')])
        rows = self.check_pair(source, translation)
        self.assertEqual([self.warnings(r) for r in rows.values()], [[]] * len(rows))


class LengthRatioTest(ContentCheckCase):
    def warned(self, translated_text, config=None):
        source, translation = build([paragraph(SOURCE_26_CHARS, translated_text)])
        if config:
            self.extra_experiment.update(config)
            self.write_json(self.config_path, self.experiment())
        return "length-ratio" in self.warnings(self.check_pair(source, translation)["9000001-001"])

    def test_too_short_or_too_long_for_the_direction_is_a_warning_and_a_normal_length_is_not(self):
        for name, text, expected in (("short", "OK.", True), ("long", "word " * 100, True), ("normal", "word " * 15, False)):
            with self.subTest(name):
                self.assertEqual(self.warned(text), expected)
                shutil.rmtree(self.data_dir)

    def test_the_range_is_set_per_direction_in_the_experiment_config(self):
        self.assertFalse(self.warned("OK.", {"length_ratio": {"trad-to-en": {"min": 0.05, "max": 50}}}))

    def test_a_tighter_range_warns_about_a_normal_length(self):
        self.assertTrue(self.warned("word " * 15, {"length_ratio": {"trad-to-en": {"min": 3, "max": 4}}}))

    def test_an_empty_source_has_no_ratio_to_check(self):
        source, translation = build([("image", {"caption": ""}, {"caption": ""})])
        self.assertEqual(self.warnings(self.check_pair(source, translation)["9000001-001"]), [])


class LengthRatioConfigTest(ContentCheckCase):
    def test_invalid_ranges_are_rejected(self):
        cases = {
            "min not below max": {"trad-to-en": {"min": 4, "max": 3}},
            "min not positive": {"trad-to-en": {"min": 0, "max": 3}},
            "direction not supported yet": {"trad-to-simplified": {"min": 1, "max": 2}},
            "unknown field": {"trad-to-en": {"min": 1, "max": 2, "mean": 1.5}},
        }
        for name, ratio in cases.items():
            with self.subTest(name):
                self.extra_experiment["length_ratio"] = ratio
                self.write_json(self.config_path, self.experiment())
                self.assert_fails_cleanly("length_ratio")


class NumberTest(ContentCheckCase):
    def missing(self, source_text, translated_text):
        source, translation = build([paragraph(source_text, translated_text)])
        row = self.check_pair(source, translation)["9000001-001"]
        shutil.rmtree(self.data_dir)
        return [w for w in row["warnings"] if w["kind"] == "number-missing"]

    def test_numbers_that_are_rendered_in_any_acceptable_way_are_found(self):
        cases = [
            ("涉款1,500萬元，足足一段說明文字。", "He is accused of HK$15 million over a long note."),
            ("涉款1500萬元，足足一段說明文字。", "He is accused of 15,000,000 dollars over a long note."),
            ("判囚3年，足足一段說明文字。", "He was jailed for three years in a long note."),
            ("判囚12年，足足一段說明文字。", "He was jailed for twelve years in a long note."),
            ("判囚25年，足足一段說明文字。", "He was jailed for twenty-five years in a long note."),
            ("判囚100年，足足一段說明文字。", "He was jailed for one hundred years in a long note."),
            ("判囚250年，足足一段說明文字。", "He was jailed for two hundred and fifty years in a long note."),
            ("2億人，足足一段說明文字。", "200 million people in a long note."),
            ("5萬人，足足一段說明文字。", "50,000 people in a long note."),
            ("5萬人，足足一段說明文字。", "50 thousand people in a long note."),
            ("5月3日發生，足足一段說明文字。", "It happened on May 3 in a long note."),
            ("9月發生，足足一段說明文字。", "It happened in Sept. in a long note."),
            ("2030年，足足一段說明文字。", "In 2030 in a long note."),
            ("增加50%，足足一段說明文字。", "An increase of 50 percent in a long note."),
            ("長3.5公里，足足一段說明文字。", "It is 3.50 km long in a long note."),
            ("沒有數字，足足一段說明文字。", "There are no numbers in a long note."),
            ("第3名，足足一段說明文字。", "He came third in a long note."),
            ("第2名，足足一段說明文字。", "He came second in a long note."),
            ("1名男子，足足一段說明文字。", "A man was arrested in a long note."),   # 1 常寫成冠詞 a／an，沒辦法判斷，不警示
        ]
        for source_text, translated_text in cases:
            with self.subTest(source_text):
                self.assertEqual(self.missing(source_text, translated_text), [])

    def test_a_number_that_is_not_in_the_translation_is_a_warning_that_names_it(self):
        cases = [
            ("涉款1500萬元，足足一段說明文字。", "He is accused of HK$14 million over a long note.", "1500萬"),
            ("2030年，足足一段說明文字。", "Some time in the future in a long note.", "2030"),
            ("5月發生，足足一段說明文字。", "It happened in June in a long note.", "5月"),
            ("判囚3年，足足一段說明文字。", "He was jailed for four years in a long note.", "3"),
            ("1月發生，足足一段說明文字。", "It happened in June in a long note.", "1月"),   # 月份的 1 照樣核對
        ]
        for source_text, translated_text, token in cases:
            with self.subTest(source_text):
                [warning] = self.missing(source_text, translated_text)
                self.assertIn(token, warning["detail"])


class QuoteCountTest(ContentCheckCase):
    def warned(self, source_text, translated_text):
        source, translation = build([paragraph(source_text, translated_text)])
        row = self.check_pair(source, translation)["9000001-001"]
        shutil.rmtree(self.data_dir)
        return "quote-count" in self.warnings(row)

    def test_a_different_number_of_direct_quotes_is_a_warning(self):
        padding = "，足足一段說明文字"   # 讓長度比例落在範圍內，只看引語
        words = " in a long enough note of words"
        cases = [
            ("他說「甲」，又說「乙」" + padding, 'He said "A" and "B"' + words, False),
            ("他說「甲」，又說「乙」" + padding, 'He said "A"' + words, True),
            ("他說「甲『乙』丙」" + padding, 'He said "A"' + words, False),      # 巢狀引號只算一個
            ("他說“甲”" + padding, "He said “A”" + words, False),
            ("他說「甲」" + padding, "He said A" + words, True),
            ("他說「甲」，又說「乙」" + padding, "He said 'A' and 'B'" + words, False),          # 直接引語用單引號
            ("他說「甲」" + padding, "He said 'A,' and left" + words, False),
            ("他說「甲『乙』丙」" + padding, "He said \"A 'B' C\"" + words, False),            # 雙引號裡的單引號是巢狀
            ("他沒有說話" + padding, "It isn't the workers' fault, John's friend said nothing" + words, False),   # 撇號不是引號
            ("他說「甲」" + padding, "It isn't the workers' fault" + words, True),
            ("他沒有說話" + padding, "He said nothing" + words, False),
            ("他沒有說話" + padding, 'He said "nothing"' + words, True),
        ]
        for source_text, translated_text, expected in cases:
            with self.subTest(source_text, translated_text=translated_text):
                self.assertEqual(self.warned(source_text, translated_text), expected)


class SuspectedRefusalTest(ContentCheckCase):
    LONG_SOURCE = "虛構市政廳翻新工程嚴重延誤" * 6

    def warnings_for(self, translated_text):
        source, translation = build([paragraph(self.LONG_SOURCE, translated_text)])
        return self.warnings(self.check_pair(source, translation)["9000001-001"])

    def test_refusal_wording_and_far_shorter_than_the_source_is_a_warning(self):
        self.assertIn("suspected-refusal", self.warnings_for("I'm sorry, but I can't help with that."))

    def test_refusal_wording_in_a_translation_of_normal_length_is_not(self):
        text = "I'm sorry, he shouted, and I can't help it. " + "word " * 40   # 有拒絕語，但長度正常
        self.assertNotIn("suspected-refusal", self.warnings_for(text))

    def test_short_but_not_refusal_wording_is_not_a_refusal_warning(self):
        self.assertNotIn("suspected-refusal", self.warnings_for("Delayed."))


class UnchangedTranslatableTest(ContentCheckCase):
    def test_identical_text_that_has_chinese_in_it_was_left_untranslated(self):
        text = "虛構市政廳翻新工程延誤，足足一段說明文字"
        source, translation = build([paragraph(text, text)])
        self.assertIn("unchanged-translatable", self.warnings(self.check_pair(source, translation)["9000001-001"]))

    def test_identical_text_without_chinese_has_nothing_to_translate(self):
        source, translation = build([("heading", {"text": "COVID-19 and ABC"}, {"text": "COVID-19 and ABC"})])
        self.assertNotIn("unchanged-translatable", self.warnings(self.check_pair(source, translation)["9000001-001"]))

    def test_translated_text_is_not_flagged(self):
        source, translation = build([paragraph("虛構市政廳翻新工程延誤", "The renovation of the fictional hall is late")])
        self.assertNotIn("unchanged-translatable", self.warnings(self.check_pair(source, translation)["9000001-001"]))


class UntranslatableChangedTest(ContentCheckCase):
    EMBEDS = {
        "code": ({"code": "<p>x</p>"}, {"code": "<p>changed</p>"}),
        "video": ({"caption": "片名"}, {"caption": "Title"}),
        "faq": ({"items": [{"question": "問", "answer": "答"}]}, {"items": [{"question": "Q", "answer": "答"}]}),
        "table": ({"name": "表", "description": "", "caption": "", "rows": [["甲"]]}, {"name": "表", "description": "", "caption": "", "rows": [["A"]]}),
    }

    def test_an_embedded_component_that_was_changed_is_a_warning(self):
        for type_, (source_fields, changed_fields) in self.EMBEDS.items():
            with self.subTest(type_):
                source, translation = build([(type_, source_fields, changed_fields)])
                row = self.check_pair(source, translation)["9000001-001"]
                self.assertEqual(self.warnings(row), ["untranslatable-changed"])
                self.assertEqual(row["findings"], [])
                shutil.rmtree(self.data_dir)

    def test_an_embedded_component_copied_back_unchanged_is_fine(self):
        for type_, (source_fields, _) in self.EMBEDS.items():
            with self.subTest(type_):
                source, translation = build([(type_, source_fields, source_fields)])
                self.assertEqual(self.warnings(self.check_pair(source, translation)["9000001-001"]), [])
                shutil.rmtree(self.data_dir)

    def test_a_changed_type_is_a_finding_not_also_a_changed_embed(self):
        source, translation = build([("code", {"code": "<p>x</p>"}, {"code": "<p>x</p>"})])
        translation["components"][0]["type"] = "paragraph"
        row = self.check_pair(source, translation)["9000001-001"]
        self.assertEqual(self.findings(row), [("Locale & Format", "type-changed", "minor")])
        self.assertEqual(self.warnings(row), [])


if __name__ == "__main__":
    unittest.main()
