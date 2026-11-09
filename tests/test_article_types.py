"""文章 Component type 的清單要與現有的轉換器（tools/hk01_web.py）一致：轉換器多產生一種 type，
這裡就會失敗，而不是讓內容規則靜默地略過它。ground truth 的 domain 與事實種類也一樣：標準庫的渲染工具（tools/render_ground_truth.py）
不能 import 這個 package，所以它抄了一份清單，這裡守住兩份不會悄悄分歧。"""
import re
import unittest
from pathlib import Path

from translation_eval.articles import KNOWN_TYPES, MAIN_TYPES
from translation_eval.schemas import Domain, FactKind

CONVERTER = Path(__file__).resolve().parent.parent / "tools" / "hk01_web.py"
RENDERER = Path(__file__).resolve().parent.parent / "tools" / "render_ground_truth.py"


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


class GroundTruthRendererTest(unittest.TestCase):
    def source(self) -> str:
        return RENDERER.read_text(encoding="utf-8")

    def test_the_renderers_domains_are_the_ones_the_schema_has(self):
        match = re.search(r"^DOMAINS = \{([^}]*)\}", self.source(), re.M)
        self.assertIsNotNone(match, "找不到渲染工具的 DOMAINS：這個測試的搜尋寫法要跟著 render_ground_truth.py 改")
        self.assertEqual(set(re.findall(r'"([a-z-]+)"', match.group(1))), {d.value for d in Domain})

    def test_the_renderers_fact_kinds_are_the_ones_the_schema_has(self):
        match = re.search(r"^KIND_LABEL = \{(.*?)^\}", self.source(), re.M | re.S)
        self.assertIsNotNone(match, "找不到渲染工具的 KIND_LABEL：這個測試的搜尋寫法要跟著 render_ground_truth.py 改")
        self.assertEqual(set(re.findall(r'"([a-z_]+)":', match.group(1))), {k.value for k in FactKind})


if __name__ == "__main__":
    unittest.main()
