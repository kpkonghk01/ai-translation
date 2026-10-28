"""compare_global 的測試（不需要 OpenCC）。用法：python3 -I tools/test_compare_global.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_global as G  # noqa: E402

TABLE = str.maketrans({"強": "强", "姦": "奸", "裏": "里", "軟": "软", "體": "体"})
conv = lambda s: s.translate(TABLE)  # noqa: E731


class CompareTests(unittest.TestCase):
    def test_exact_match_after_conversion(self):
        r = G.compare(["強姦案", "軟體"], ["强奸案", "软体"], conv)
        self.assertEqual((r["matched"], r["total"], r["no_counterpart"], r["extra_in_global"]), (2, 2, 0, 0))

    def test_order_does_not_matter_and_duplicates_are_consumed_once(self):
        r = G.compare(["甲", "甲"], ["甲"], conv)
        self.assertEqual((r["matched"], r["no_counterpart"]), (1, 1))

    def test_extra_global_item_is_counted(self):
        r = G.compare(["軟體"], ["软体", "多出的圖說"], conv)
        self.assertEqual((r["matched"], r["extra_in_global"]), (1, 1))

    def test_char_level_difference_is_collected(self):
        # 簡體版用了「里」，而轉換器輸出「裏」→ 這個差異要被統計出來
        r = G.compare(["這裏有一個很長的句子用來比對"], ["这里有一个很长的句子用来比对"], lambda s: s.translate(str.maketrans({"這": "这", "個": "个", "來": "来", "對": "对"})))
        self.assertEqual(r["matched"], 0)
        self.assertEqual(r["pairs"][("裏", "里")], 1)

    def test_rewritten_paragraph_has_no_counterpart(self):
        r = G.compare(["完全不同的一段文字內容在這裡"], ["xyz"], conv)
        self.assertEqual((r["matched"], r["no_counterpart"]), (0, 1))

    def test_whitespace_is_ignored(self):
        self.assertEqual(G.norm(" 甲 \n乙　丙 "), "甲乙丙")

    def test_text_extractors(self):
        art = {"components": [{"type": "paragraph", "text": "甲"}, {"type": "image", "caption": "圖"}, {"type": "quote", "text": "乙", "source": "丙"}, {"type": "heading", "text": " "}]}
        self.assertEqual(G.trad_texts(art), ["甲", "乙"])
        self.assertEqual(G.glob_texts({"items": [["p", "甲"], ["h3", ""], ["p", "乙 丙"]]}), ["甲", "乙丙"])


if __name__ == "__main__":
    unittest.main()
