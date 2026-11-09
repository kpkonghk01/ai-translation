"""文章 Component type 的清單要與現有的轉換器（tools/hk01_web.py）一致：轉換器多產生一種 type，
這裡就會失敗，而不是讓內容規則靜默地略過它。"""
import re
import unittest
from pathlib import Path

from translation_eval.articles import KNOWN_TYPES

CONVERTER = Path(__file__).resolve().parent.parent / "tools" / "hk01_web.py"


class ArticleTypesTest(unittest.TestCase):
    def test_every_type_the_converter_emits_is_known(self):
        emitted = set(re.findall(r'add\(type="([a-z-]+)"', CONVERTER.read_text(encoding="utf-8")))
        self.assertTrue(emitted, "找不到轉換器產生的 type：這個測試的搜尋寫法要跟著 hk01_web.py 改")
        self.assertEqual(emitted - KNOWN_TYPES, set())

    def test_every_known_type_is_one_the_converter_emits(self):
        emitted = set(re.findall(r'add\(type="([a-z-]+)"', CONVERTER.read_text(encoding="utf-8")))
        self.assertEqual(KNOWN_TYPES - emitted, set())


if __name__ == "__main__":
    unittest.main()
