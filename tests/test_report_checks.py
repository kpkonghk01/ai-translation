"""report 對 Deterministic check 的呈報：Finding 按 Dimension、警示按類別的數量，以及 check 結果過期時不產生報告。"""
import copy
import json
import os
import unittest

from content_cases import ContentCheckCase, build, paragraph
from fixtures import TERM_LIST

SOURCE_26_CHARS = "虛構市政廳翻新工程嚴重延誤" * 2
SOURCE_78_CHARS = "虛構市政廳翻新工程嚴重延誤" * 6


class CheckCountsTest(ContentCheckCase):
    def test_deterministic_findings_are_counted_per_dimension(self):
        source, translation = build([
            paragraph("市民陳虛構表示，足足一段說明文字。", "Resident 陳 said"),   # Terminology 1（沒有指定譯法）、Locale & Format 1（殘留「陳」）
            paragraph("第二段文字，足足一段說明文字。", "gone"),                 # 譯文會被拿掉：Accuracy 1
        ])
        translation["components"].pop()
        self.check_pair(source, translation, TERM_LIST)
        self.assertIn("- Deterministic Finding：Accuracy 1、Terminology 1、Fluency 0、Fidelity-of-force 0、Locale & Format 1"
                      "（已檢查 1 個 ok 的 Run、共 4 個 Component）", self.report_text())

    def test_warnings_are_counted_per_kind(self):
        source, translation = build([
            paragraph(SOURCE_26_CHARS, "OK."),                                                   # 長度比例
            paragraph("涉款2030萬元，足足一段說明文字。", "He is accused over a long note of words here ok."),   # 數字遺失
            paragraph("他說「甲」，足足一段說明文字。", "He said A in a long note of words here ok."),         # 引語數量
            paragraph(SOURCE_78_CHARS, "I'm sorry, but I can't help with that."),                # 疑似 Refusal（同時長度比例）
            paragraph("完全相同的中文，沒有翻譯足足一段", "完全相同的中文，沒有翻譯足足一段"),                       # 譯文與原文相同（同時長度比例）
            ("code", {"code": "<p>x</p>"}, {"code": "<p>changed</p>"}),                          # 不翻譯的 Component 被改動
        ])
        self.check_pair(source, translation)
        self.assertIn("- 警示：長度比例 3、數字遺失 1、引語數量 1、疑似 Refusal 1、譯文與原文相同 1、不翻譯的 Component 被改動 1",
                      self.report_text())

    def test_the_section_says_so_when_there_is_nothing_to_report(self):
        source, translation = build([paragraph("甲乙丙丁戊己庚辛壬癸。", "Ten letters worth of clean English.")])
        self.check_pair(source, translation)
        text = self.report_text()
        self.assertIn("- Deterministic Finding：Accuracy 0、Terminology 0、Fluency 0、Fidelity-of-force 0、Locale & Format 0", text)
        self.assertIn("- 警示：長度比例 0、數字遺失 0、引語數量 0、疑似 Refusal 0、譯文與原文相同 0、不翻譯的 Component 被改動 0", text)


class HeaderTest(ContentCheckCase):
    def test_the_header_lists_the_term_list_and_the_length_ranges(self):
        source, translation = build([paragraph("甲乙丙丁戊己庚辛壬癸。", "Ten letters worth of clean English.")])
        self.check_pair(source, translation, TERM_LIST)
        text = self.report_text()
        self.assertRegex(text, r"- Term List：目前 [0-9a-f]{64}（3 條）")
        self.assertRegex(text, r"- Run 紀錄裡的 Term List hash：[0-9a-f]{64}（1 個 Run）")
        self.assertIn("- 長度比例範圍（警示用）：trad-to-en 1.5–8.0", text)

    def test_the_header_says_when_no_term_list_is_used(self):
        source, translation = build([paragraph("甲乙丙丁戊己庚辛壬癸。", "Ten letters worth of clean English.")])
        self.check_pair(source, translation)
        text = self.report_text()
        self.assertIn("- Term List：未使用", text)
        self.assertIn("- Run 紀錄裡的 Term List hash：未使用（1 個 Run）", text)

    def test_a_configured_length_range_is_shown_instead_of_the_default(self):
        self.extra_experiment["length_ratio"] = {"trad-to-en": {"min": 2, "max": 6.5}}
        self.write_json(self.config_path, self.experiment())
        source, translation = build([paragraph("甲乙丙丁戊己庚辛壬癸。", "Ten letters worth of clean English.")])
        self.check_pair(source, translation)
        self.assertIn("- 長度比例範圍（警示用）：trad-to-en 2.0–6.5", self.report_text())


class StaleCheckTest(ContentCheckCase):
    """check 的結果取決於規則、Term List 和長度比例範圍；它們變了而沒有重跑 check，報告就會靜默地用舊結果。"""

    def checked(self):
        source, translation = build([paragraph("甲乙丙丁戊己庚辛壬癸。", "Ten letters worth of clean English.")])
        self.check_pair(source, translation, TERM_LIST)

    def assert_report_refuses(self):
        report = os.path.join(self.out_dir, "report.md")
        if os.path.exists(report):
            os.remove(report)
        code, _, err = self.run_cli("report")
        self.assertNotEqual(code, 0)
        self.assertIn("重新執行 check", err)
        self.assertFalse(os.path.exists(report))

    def test_a_changed_term_list_makes_the_check_results_stale(self):
        self.checked()
        changed = copy.deepcopy(TERM_LIST)
        changed["terms"][0]["translations"]["trad-to-en"] = ["Another Rendering"]
        self.write_json(self.extra_experiment["term_list"], changed)
        self.assert_report_refuses()
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)   # 重跑 check 之後就好了

    def test_a_changed_length_range_makes_the_check_results_stale(self):
        self.checked()
        self.extra_experiment["length_ratio"] = {"trad-to-en": {"min": 2, "max": 6}}
        self.write_json(self.config_path, self.experiment())
        self.assert_report_refuses()

    def test_a_term_list_added_after_the_check_makes_it_stale(self):
        source, translation = build([paragraph("甲乙丙丁戊己庚辛壬癸。", "Ten letters worth of clean English.")])
        self.check_pair(source, translation)
        self.use_term_list(TERM_LIST)
        self.assert_report_refuses()

    def test_results_from_an_older_rule_set_are_stale(self):
        self.checked()
        meta_path = os.path.join(self.out_dir, "check-meta.json")
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["rules_version"] = "deterministic.v1"
        self.write_json(meta_path, meta)
        self.assert_report_refuses()

    def test_a_check_that_left_no_meta_file_is_stale(self):
        self.checked()
        os.remove(os.path.join(self.out_dir, "check-meta.json"))
        self.assert_report_refuses()


if __name__ == "__main__":
    unittest.main()
