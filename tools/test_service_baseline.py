"""service_baseline 的測試（不需要 OpenCC）。用法：python3 -I tools/test_service_baseline.py"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import service_baseline as S  # noqa: E402

CFG = {
    "name": "Traditional Chinese to Simplified Chinese",
    "segmentation": {"type": "mmseg", "dict": {"type": "ocd2", "file": "../../../../node_modules/opencc/prebuilds/assets/TSPhrases.ocd2"}},
    "conversion_chain": [
        {"dict": {"type": "group", "dicts": [
            {"type": "ocd2", "file": "./dict/ocd2/TSPhrasesCust.ocd2"},
            {"type": "ocd2", "file": "../../../../node_modules/opencc/prebuilds/assets//HKVariantsRev.ocd2"},
        ]}},
        {"dict": {"type": "ocd2", "file": "./dict/ocd2/HKRevPhrasesCust.ocd2"}},
    ],
}


class RewriteTests(unittest.TestCase):
    def setUp(self):
        self.out = S.rewrite_config(CFG, "/svc/cfg", "/stock")

    def test_stock_dicts_point_to_stock_dir_even_with_double_slash(self):
        self.assertEqual(self.out["segmentation"]["dict"]["file"], "/stock/TSPhrases.ocd2")
        group = self.out["conversion_chain"][0]["dict"]["dicts"]
        self.assertEqual(group[1]["file"], "/stock/HKVariantsRev.ocd2")

    def test_custom_dicts_become_absolute_paths_next_to_the_config(self):
        group = self.out["conversion_chain"][0]["dict"]["dicts"]
        self.assertEqual(group[0]["file"], "/svc/cfg/dict/ocd2/TSPhrasesCust.ocd2")
        self.assertEqual(self.out["conversion_chain"][1]["dict"]["file"], "/svc/cfg/dict/ocd2/HKRevPhrasesCust.ocd2")

    def test_order_and_other_fields_are_untouched(self):
        self.assertEqual(self.out["name"], CFG["name"])
        self.assertEqual(self.out["segmentation"]["type"], "mmseg")
        self.assertEqual([d["type"] for d in self.out["conversion_chain"][0]["dict"]["dicts"]], ["ocd2", "ocd2"])

    def test_input_is_not_mutated(self):
        self.assertTrue(CFG["segmentation"]["dict"]["file"].startswith("../../"))


class ContextTests(unittest.TestCase):
    def test_context_drops_meta_and_is_compact_json(self):
        art = {"title": "標題", "mainChannel": "社會新聞", "tags": [], "components": [{"id": "1-001", "type": "paragraph", "text": "他說「好」"}], "meta": {"x": 1}}
        ctx = S.context_string(art)
        self.assertNotIn("meta", ctx)
        self.assertNotIn(" ", ctx.replace("他說「好」", ""))  # 緊湊，無多餘空白
        self.assertIn("他說「好」", ctx)  # 不可轉成 \u 逸出

    def test_apply_to_article_roundtrips_and_keeps_meta(self):
        art = {"title": "甲", "mainChannel": "乙", "tags": ["丙"], "components": [{"id": "1-001", "type": "paragraph", "text": "丁"}], "meta": {"articleId": 1}}
        out = S.apply_to_article(art, lambda s: s.replace("丁", "戊"))
        self.assertEqual(out["components"][0]["text"], "戊")
        self.assertEqual(out["meta"], {"articleId": 1})
        self.assertEqual(art["components"][0]["text"], "丁")  # 原物件不變


class LoadArticlesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, name):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump({"title": name}, f)

    def test_reads_web_articles_and_cms_session_ids(self):
        for name in ("123.json", "a0a0a0a0a0a0a0a0a0a0a0a0.json", "fetch-log.tsv"):
            self.write(name)
        self.assertEqual([aid for aid, _ in S.load_articles(self.dir)], ["123", "a0a0a0a0a0a0a0a0a0a0a0a0"])

    def test_a_json_file_that_is_not_an_article_id_fails_instead_of_being_skipped(self):
        for name in ("123.json", "notes.json"):
            self.write(name)
        with self.assertRaises(ValueError) as caught:
            list(S.load_articles(self.dir))
        self.assertIn("notes.json", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
