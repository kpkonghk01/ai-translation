"""文章 Component type 的清單要與現有的轉換器（tools/hk01_web.py）一致：轉換器多產生一種 type，
這裡就會失敗，而不是讓內容規則靜默地略過它。"""
import re
import unittest
from pathlib import Path

from translation_eval.articles import KNOWN_TYPES, MAIN_TYPES

CONVERTER = Path(__file__).resolve().parent.parent / "tools" / "hk01_web.py"


class ArticleTypesTest(unittest.TestCase):
    def test_every_type_the_converter_emits_is_known(self):
        emitted = set(re.findall(r'add\(type="([a-z-]+)"', CONVERTER.read_text(encoding="utf-8")))
        self.assertTrue(emitted, "找不到轉換器產生的 type：這個測試的搜尋寫法要跟著 hk01_web.py 改")
        self.assertEqual(emitted - KNOWN_TYPES, set())

    def test_every_known_type_is_one_the_converter_emits(self):
        emitted = set(re.findall(r'add\(type="([a-z-]+)"', CONVERTER.read_text(encoding="utf-8")))
        self.assertEqual(KNOWN_TYPES - emitted, set())

    def test_the_main_component_types_are_the_ones_the_converter_calls_main(self):
        match = re.search(r"^MAIN_TYPES = \{([^}]*)\}", CONVERTER.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(match, "找不到轉換器的 MAIN_TYPES：這個測試的搜尋寫法要跟著 hk01_web.py 改")
        self.assertEqual(set(re.findall(r'"([a-z-]+)"', match.group(1))), set(MAIN_TYPES))


if __name__ == "__main__":
    unittest.main()
