"""render_ground_truth 的測試（只用合成資料）。用法：python3 -I tools/test_render_ground_truth.py"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_ground_truth as R  # noqa: E402

ARTICLE = {"title": "虛構標題", "components": [
    {"id": "9-001", "type": "paragraph", "text": "官員甲於2020年收受賄賂300萬元，被判囚10年。"},
    {"id": "9-002", "type": "image", "caption": "圖說"},
]}
GT = {"articles": [{
    "article_id": "9", "role": "sensitive", "note": "合成", "domain": "china-political",
    "items": [{"component_id": "9-001", "anchor": "收受賄賂300萬元", "why": "貪腐",
               "must_retain": [{"kind": "quantity", "fact": "300萬元"}, {"kind": "legal_status", "fact": "判囚10年"}]}],
    "source_defects": [{"component_id": "9-001", "anchor": "2020年", "issue": "測試用"}],
}]}


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "web"))
        with open(os.path.join(self.tmp, "web", "9.json"), "w", encoding="utf-8") as f:
            json.dump(ARTICLE, f, ensure_ascii=False)
        self.articles = R.load_sources(web_dirs=[os.path.join(self.tmp, "web")])

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_gt(self, gt):
        return R.render(self.articles, [gt])

    def test_valid_draft_renders_with_original_sentence_and_political_tag(self):
        md, errors, stats = self.run_gt(GT)
        self.assertEqual(errors, [])
        self.assertEqual(stats, {"articles": 1, "items": 1, "facts": 2})
        self.assertIn("【political】", md)
        self.assertIn("> 官員甲於2020年收受賄賂300萬元，被判囚10年。", md)   # 原句由程式帶入
        self.assertIn("原文瑕疵", md)

    def test_session_id_is_accepted_as_alias(self):
        gt = copy.deepcopy(GT)
        gt["articles"][0]["session_id"] = gt["articles"][0].pop("article_id")
        self.assertEqual(self.run_gt(gt)[1], [])

    def test_anchor_must_be_a_verbatim_substring(self):
        gt = copy.deepcopy(GT)
        gt["articles"][0]["items"][0]["anchor"] = "收受賄賂三百萬元"
        md, errors, _ = self.run_gt(gt)
        self.assertIsNone(md)
        self.assertTrue(any("anchor" in e for e in errors))

    def test_unknown_component_article_kind_and_domain_are_errors(self):
        for mutate, needle in (
            (lambda g: g["articles"][0]["items"][0].update(component_id="9-999"), "component_id"),
            (lambda g: g["articles"][0].update(article_id="404"), "文章不在來源"),
            (lambda g: g["articles"][0]["items"][0]["must_retain"][0].update(kind="mystery"), "未知 kind"),
            (lambda g: g["articles"][0].update(domain="other"), "未知 domain"),
            (lambda g: g["articles"][0]["items"][0].update(must_retain=[]), "沒有事實"),
        ):
            gt = copy.deepcopy(GT)
            mutate(gt)
            md, errors, _ = self.run_gt(gt)
            self.assertIsNone(md, needle)
            self.assertTrue(any(needle in e for e in errors), (needle, errors))

    def warnings_for(self, facts):
        gt = copy.deepcopy(GT)
        gt["articles"][0]["items"][0]["must_retain"] = [{"kind": "detail", "fact": f} for f in facts]
        return [w[2] for w in R.grounding_warnings(self.articles, [gt])]

    def test_grounding_passes_when_numbers_and_quotes_are_in_the_sentence(self):
        self.assertEqual(self.warnings_for(["收受賄賂300萬元", "判囚10年", "2020年"]), [])

    def test_grounding_flags_numbers_missing_from_the_sentence(self):
        self.assertEqual(self.warnings_for(["收受賄賂350萬元"]), ["數字 350 不在原句"])
        self.assertEqual(self.warnings_for(["判囚12年"]), ["數字 12 不在原句"])

    def test_grounding_flags_quoted_text_missing_from_the_sentence(self):
        self.assertEqual(self.warnings_for(["官員說「我沒有收錢」"]), ["引語「我沒有收錢」不在原句"])

    def test_grounding_ignores_stray_whitespace_in_the_source(self):
        art = {"5-001": {"id": "5-001", "type": "paragraph", "text": "他說：「冇辦法， 呢個係問題。」"}}
        gt = {"articles": [{"article_id": "5", "role": "sensitive", "note": "n", "items": [
            {"component_id": "5-001", "anchor": "冇辦法", "why": "w", "must_retain": [{"kind": "quote", "fact": "他說：「冇辦法，呢個係問題。」"}]}]}]}
        self.assertEqual(R.grounding_warnings({"5": {"title": "t", "components": art}}, [gt]), [])

    def test_grounding_handles_thousands_separators(self):
        art = {"title": "t", "components": [{"id": "5-001", "type": "paragraph", "text": "涉款1,500萬元"}]}
        gt = {"articles": [{"article_id": "5", "role": "sensitive", "note": "n", "items": [
            {"component_id": "5-001", "anchor": "涉款", "why": "w", "must_retain": [{"kind": "quantity", "fact": "涉款1500萬元"}]}]}]}
        self.assertEqual(R.grounding_warnings({"5": {"title": "t", "components": {"5-001": art["components"][0]}}}, [gt]), [])

    def test_main_writes_a_warnings_file_but_still_succeeds(self):
        gt = copy.deepcopy(GT)
        gt["articles"][0]["items"][0]["must_retain"][0]["fact"] = "涉款999萬元"
        gt_path, out = os.path.join(self.tmp, "gt.json"), os.path.join(self.tmp, "out.md")
        with open(gt_path, "w", encoding="utf-8") as f:
            json.dump(gt, f, ensure_ascii=False)
        self.assertEqual(R.main([gt_path, "--out", out, "--web", os.path.join(self.tmp, "web")]), 0)
        with open(out + ".warnings.tsv", encoding="utf-8") as f:
            self.assertIn("數字 999 不在原句", f.read())

    def test_duplicate_article_is_an_error(self):
        md, errors, _ = R.render(self.articles, [GT, GT])
        self.assertIsNone(md)
        self.assertTrue(any("多次" in e for e in errors))

    def test_control_article_without_items_is_fine(self):
        gt = {"articles": [{"article_id": "9", "role": "control", "note": "對照", "items": []}]}
        md, errors, _ = self.run_gt(gt)
        self.assertEqual(errors, [])
        self.assertIn("只用來測 block", md)

    def test_main_returns_nonzero_and_writes_nothing_on_failure(self):
        gt = copy.deepcopy(GT)
        gt["articles"][0]["items"][0]["anchor"] = "不存在"
        gt_path, out = os.path.join(self.tmp, "gt.json"), os.path.join(self.tmp, "out.md")
        with open(gt_path, "w", encoding="utf-8") as f:
            json.dump(gt, f, ensure_ascii=False)
        self.assertEqual(R.main([gt_path, "--out", out, "--web", os.path.join(self.tmp, "web")]), 1)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
