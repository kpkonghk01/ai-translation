"""繁→簡：翻譯 prompt、殘留繁體字（OpenCC t2s）、其餘規則在簡體譯文上的行為。只用虛構文字。"""
import hashlib
import json
import shutil
import unittest
from pathlib import Path

import translation_eval
from content_cases import ContentCheckCase, build, paragraph
from fake_transport import FakeTransport
from fixtures import Workspace, simplified_response


def system_prompt(request) -> str:
    return json.loads(request.body)["systemInstruction"]["parts"][0]["text"]


class TranslateTest(Workspace):
    directions = ["trad-to-simplified"]

    def sent(self):
        transport = FakeTransport(simplified_response())
        self.assertEqual(self.run_cli("translate", transport=transport)[0], 0)
        return transport.requests[0]

    def test_the_prompt_asks_for_natural_mainland_written_chinese_and_keeps_cantonese_profanity(self):
        prompt = system_prompt(self.sent())
        self.assertIn("Simplified Chinese", prompt)
        self.assertIn("mainland", prompt)
        self.assertIn("Cantonese", prompt)

    def test_the_run_record_has_the_direction_and_the_id_and_hash_of_the_simplified_prompt(self):
        self.sent()
        [record] = self.read_runs()
        template = Path(translation_eval.__file__).parent / "prompt_templates" / "translate-trad-to-simplified.txt"
        self.assertEqual((record["direction"], record["prompt_id"]), ("trad-to-simplified", "translate-trad-to-simplified"))
        self.assertEqual(record["prompt_hash"], hashlib.sha256(template.read_bytes()).hexdigest())

    def test_term_list_entries_with_a_simplified_rendering_are_injected_and_english_only_ones_are_not(self):
        self.use_term_list({"terms": [
            {"source": "虛構市政廳", "category": "organization", "translations": {"trad-to-simplified": ["虚构市政厅（简）"]}},
            {"source": "陳虛構", "category": "person", "translations": {"trad-to-en": ["Chan Fictional"]}},
        ]})
        prompt = system_prompt(self.sent())
        self.assertIn("虚构市政厅（简）", prompt)
        self.assertNotIn("Chan Fictional", prompt)


class ResidualTraditionalTest(ContentCheckCase):
    directions = ["trad-to-simplified"]

    def residue(self, row):
        return [f["target_span"] for f in row["findings"] if f["subtype"] == "residual-traditional"]

    def test_each_character_t2s_would_change_is_one_minor_locale_finding_once_per_component(self):
        source, translation = build([paragraph("他說虛構的問題。", "他說虛虛構的问题。")])
        row = self.check_pair(source, translation)["9000001-001"]
        self.assertEqual(self.residue(row), ["說", "虛", "構"])   # 依第一次出現的順序；「虛」出現兩次，只記一次
        self.assertEqual({(f["dimension"], f["severity"], f["source"]) for f in row["findings"]}, {("Locale & Format", "minor", "deterministic")})

    def test_a_character_is_reported_again_in_the_next_component(self):
        source, translation = build([paragraph("甲。", "虛。"), paragraph("乙。", "虛。")])
        rows = self.check_pair(source, translation)
        self.assertEqual([self.residue(rows[c]) for c in ("9000001-001", "9000001-002")], [["虛"], ["虛"]])

    def test_clean_simplified_text_has_no_findings(self):
        source, translation = build([paragraph("他說虛構的問題。", "他说虚构的问题。")], translated_title="虚构标题", translated_tags=("虚构",))
        rows = self.check_pair(source, translation)
        self.assertEqual([r["findings"] for r in rows.values()], [[]] * len(rows))

    def test_the_title_tags_and_captions_are_checked_too(self):
        source, translation = build(
            [("gallery", {"captions": ["圖一"]}, {"captions": ["圖一"]}), ("related-article", {"titles": ["相關"]}, {"titles": ["相關"]})],
            translated_title="標題", translated_tags=("標籤",))
        rows = self.check_pair(source, translation)
        self.assertEqual(self.residue(rows["title"]), ["標", "題"])
        self.assertEqual(self.residue(rows["tags"]), ["標", "籤"])
        self.assertEqual(self.residue(rows["9000001-001"]), ["圖"])
        self.assertEqual(self.residue(rows["9000001-002"]), ["關"])   # 「相」簡繁同形，OpenCC 不改

    def test_embedded_components_may_keep_their_traditional_characters(self):
        source, translation = build([("code", {"code": "<p>虛構</p>"}, {"code": "<p>虛構</p>"})])
        self.assertEqual(self.residue(self.check_pair(source, translation)["9000001-001"]), [])

    def test_an_exception_in_the_term_list_is_not_reported_in_a_component_that_has_the_term(self):
        term_list = {"terms": [{"source": "陳虛構", "category": "person", "translations": {"trad-to-simplified": ["陈虚构"]},
                                "simplified_exceptions": ["虛"]}]}
        source, translation = build([paragraph("市民陳虛構表示。", "市民陈虛构表示。")])   # 「虛」留著，「構」沒有
        rows = self.check_pair(source, translation, term_list)
        self.assertEqual(self.residue(rows["9000001-001"]), [])   # 這個 Component 有「陳虛構」，「虛」是允許的；「構」已簡化

    def test_an_exception_does_not_apply_to_a_component_without_the_term(self):
        term_list = {"terms": [{"source": "陳虛構", "category": "person", "translations": {"trad-to-simplified": ["陈虚构"]},
                                "simplified_exceptions": ["虛"]}]}
        source, translation = build([paragraph("市民陳虛構表示。", "市民陈虛构表示。"), paragraph("另一段。", "另一段虛。")])
        rows = self.check_pair(source, translation, term_list)
        self.assertEqual(self.residue(rows["9000001-001"]), [])
        self.assertEqual(self.residue(rows["9000001-002"]), ["虛"])

    def test_an_exception_only_exempts_the_characters_it_names(self):
        term_list = {"terms": [{"source": "陳虛構", "category": "person", "translations": {"trad-to-simplified": ["陈虚构"]},
                                "simplified_exceptions": ["虛"]}]}
        source, translation = build([paragraph("市民陳虛構表示。", "市民陈虛構表示。")])
        rows = self.check_pair(source, translation, term_list)
        self.assertEqual(self.residue(rows["9000001-001"]), ["構"])

    def test_terminology_uses_the_simplified_renderings(self):
        term_list = {"terms": [{"source": "虛構市政廳", "category": "organization", "translations": {"trad-to-simplified": ["虚构市政厅"]}}]}
        source, translation = build([paragraph("虛構市政廳翻新。", "虚构市政厅翻新。"), paragraph("虛構市政廳完工。", "虚构大会堂完工。")])
        rows = self.check_pair(source, translation, term_list)
        self.assertEqual([f["subtype"] for f in rows["9000001-001"]["findings"]], [])
        self.assertEqual([f["subtype"] for f in rows["9000001-002"]["findings"]], ["term-mismatch"])

    def test_chinese_text_is_not_residual_chinese_in_this_direction(self):
        source, translation = build([paragraph("他說虛構的問題。", "他说虚构的问题。")])
        self.assertEqual([f["subtype"] for f in self.check_pair(source, translation)["9000001-001"]["findings"]], [])


class SimplifiedWarningsTest(ContentCheckCase):
    directions = ["trad-to-simplified"]

    def warnings_of(self, source_text, translated_text):
        shutil.rmtree(self.data_dir, ignore_errors=True)   # 同一個測試會呼叫好幾次；有紀錄的 Run 會被略過
        source, translation = build([paragraph(source_text, translated_text)])
        return self.warnings(self.check_pair(source, translation)["9000001-001"])

    def test_numbers_kept_in_digits_are_found_even_when_the_unit_changed_script(self):
        self.assertNotIn("number-missing", self.warnings_of("涉款1,500萬元，判囚12年，2億人。", "涉款1500万元，判囚12年，2亿人。"))

    def test_a_number_that_is_gone_is_a_warning_that_names_it(self):
        source, translation = build([paragraph("涉款2030萬元。", "涉款很多钱。")])
        [warning] = [w for w in self.check_pair(source, translation)["9000001-001"]["warnings"] if w["kind"] == "number-missing"]
        self.assertIn("2030", warning["detail"])

    def test_the_length_ratio_range_for_this_direction_applies(self):
        self.assertNotIn("length-ratio", self.warnings_of("虛構市政廳翻新工程嚴重延誤。", "虚构市政厅翻新工程严重延误。"))
        self.assertIn("length-ratio", self.warnings_of("虛構市政廳翻新工程嚴重延誤。", "虚构。"))
        self.assertIn("length-ratio", self.warnings_of("虛構市政廳翻新。", "虚构市政厅翻新工程严重延误而且预算超支很多。"))

    def test_text_left_identical_is_reported_by_the_residual_findings_not_as_unchanged(self):
        text = "虛構市政廳翻新工程嚴重延誤。"
        source, translation = build([paragraph(text, text)])
        row = self.check_pair(source, translation)["9000001-001"]
        self.assertNotIn("unchanged-translatable", self.warnings(row))
        self.assertTrue(any(f["subtype"] == "residual-traditional" for f in row["findings"]))

    def test_direct_quotes_are_counted_in_either_style_of_quote_mark(self):
        self.assertNotIn("quote-count", self.warnings_of("他說「虛構」，又說「問題」。", "他说“虚构”，又说“问题”。"))
        self.assertIn("quote-count", self.warnings_of("他說「虛構」，又說「問題」。", "他说“虚构”，又说问题。"))


if __name__ == "__main__":
    unittest.main()
