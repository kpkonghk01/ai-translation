"""import-cms：把 CMS 匯出（CSV，每列是 session id 和對話紀錄）轉成統一的文章檔。只用虛構的 CMS 格式資料。"""
import contextlib
import csv
import io
import json
import os
import shutil
import tempfile
import unittest

import gemini_samples
from fake_transport import FakeTransport
from fixtures import Workspace
from translation_eval import cli

SESSION = "a0a0a0a0a0a0a0a0a0a0a0a0"   # 虛構的 24 位 hex session id
OTHER = "b1b1b1b1b1b1b1b1b1b1b1b1"


def cms_article(title="虛構市政廳翻新工程延誤"):
    """CMS 的 user turn：與網上文章同一結構，只是沒有 meta，也沒有 mainChannel 以外的欄位。"""
    return {"title": title, "mainChannel": "社會新聞", "tags": ["虛構"], "components": [
        {"id": "c-001", "type": "highlight", "text": "翻新工程延誤半年。"},
        {"id": "c-002", "type": "paragraph", "text": "市民陳虛構表示不滿。"},
        {"id": "c-003", "type": "heading", "level": "h2", "text": "市民反應"},
        {"id": "c-004", "type": "image", "caption": "施工中的虛構市政廳"},
        {"id": "c-005", "type": "code", "code": "<div>embed</div>"},
        {"id": "c-006", "type": "video"},
    ]}


def history(article, split_text=False):
    text = json.dumps(article, ensure_ascii=False)
    middle = len(text) // 2
    parts = [{"text": text[:middle]}, {"text": text[middle:]}] if split_text else [{"text": text}]
    return [{"role": "user", "parts": parts}, {"role": "model", "parts": [{"text": "{}"}]}]


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


class CmsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.csv_path = os.path.join(self.tmp, "export.csv")
        self.out_dir = os.path.join(self.tmp, "cms-articles")

    def write_csv(self, rows):
        write_csv(self.csv_path, rows)

    def run_import(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["import-cms", "--csv", self.csv_path, "--out", self.out_dir])
        return code, out.getvalue(), err.getvalue()

    def read_output(self, session_id=SESSION):
        with open(os.path.join(self.out_dir, f"{session_id}.json"), encoding="utf-8") as f:
            return json.load(f)

    def assert_rejected(self, needle):
        code, out, err = self.run_import()
        self.assertNotEqual(code, 0)
        self.assertIn(needle, err)
        self.assertEqual(out, "")
        self.assertFalse(os.path.exists(self.out_dir), "被拒絕時不可以寫出任何東西")


class ConversionTest(CmsCase):
    def test_each_row_becomes_an_article_file_named_by_its_session_id_with_cms_as_source(self):
        self.write_csv([[SESSION, json.dumps(history(cms_article()), ensure_ascii=False)],
                        [OTHER, json.dumps(history(cms_article("另一篇")), ensure_ascii=False)]])
        code, out, err = self.run_import()
        self.assertEqual(code, 0, err)
        article = self.read_output()
        self.assertEqual({k: article[k] for k in ("title", "mainChannel", "tags", "components")}, cms_article())
        self.assertEqual(article["meta"]["source"], "cms")
        self.assertEqual(article["meta"]["articleId"], SESSION)
        self.assertEqual(article["meta"]["unknown"], [])
        self.assertEqual(self.read_output(OTHER)["title"], "另一篇")
        self.assertIn("2", out)
        self.assertNotIn("虛構", out)   # 終端只印數量

    def test_the_first_user_turn_is_used_and_its_parts_are_joined(self):
        turns = [{"role": "model", "parts": [{"text": "先是模型"}]}] + history(cms_article(), split_text=True) \
            + [{"role": "user", "parts": [{"text": "後面的 user turn 不用"}]}]
        self.write_csv([[SESSION, json.dumps(turns, ensure_ascii=False)]])
        self.assertEqual(self.run_import()[0], 0)
        self.assertEqual(self.read_output()["components"], cms_article()["components"])

    def test_converting_again_gives_the_same_files(self):
        self.write_csv([[SESSION, json.dumps(history(cms_article()), ensure_ascii=False)]])
        self.run_import()
        with open(os.path.join(self.out_dir, f"{SESSION}.json"), "rb") as f:
            first = f.read()
        self.assertEqual(self.run_import()[0], 0)
        with open(os.path.join(self.out_dir, f"{SESSION}.json"), "rb") as f:
            self.assertEqual(f.read(), first)

    def test_a_field_without_text_that_the_converter_does_not_know_is_dropped(self):
        article = cms_article()
        article["components"][1]["order"] = 3
        article["revision"] = 7
        self.write_csv([[SESSION, json.dumps(history(article), ensure_ascii=False)]])
        self.assertEqual(self.run_import()[0], 0)
        self.assertNotIn("order", self.read_output()["components"][1])
        self.assertNotIn("revision", self.read_output())


class TranslateConvertedArticlesTest(Workspace):
    """轉出來的文章檔可以直接放進實驗設定翻譯。"""
    article_ids = ()

    def test_the_converted_files_can_be_translated_as_an_experiments_articles(self):
        csv_path, out_dir = os.path.join(self.tmp, "export.csv"), os.path.join(self.tmp, "cms-articles")
        write_csv(csv_path, [[SESSION, json.dumps(history(cms_article()), ensure_ascii=False)]])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["import-cms", "--csv", csv_path, "--out", out_dir]), 0)
        self.articles_dir, self.article_ids = out_dir, (SESSION,)
        self.write_json(self.config_path, self.experiment())
        translation = {"title": "Fictional city hall renovation delayed", "tags": ["Fictional"], "components": [
            {"id": "c-001", "type": "highlight", "text": "The renovation is six months late."},
            {"id": "c-002", "type": "paragraph", "text": "Resident Chan Fictional voiced his anger."},
            {"id": "c-003", "type": "heading", "level": "h2", "text": "Residents react"},
            {"id": "c-004", "type": "image", "caption": "The fictional city hall under construction"},
            {"id": "c-005", "type": "code", "code": "<div>embed</div>"},
            {"id": "c-006", "type": "video"}]}

        code, _, err = self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(translation))))

        self.assertEqual(code, 0, err)
        self.assertEqual(self.read_runs()[0]["outcome"], "ok")


class RejectionTest(CmsCase):
    def test_an_unknown_component_type_is_rejected_and_listed_with_the_session_id(self):
        article = cms_article()
        article["components"].append({"id": "c-007", "type": "poll", "text": "投票"})
        self.write_csv([[SESSION, json.dumps(history(article), ensure_ascii=False)]])
        self.assert_rejected("poll")
        self.assertIn(SESSION, self.run_import()[2])

    def test_visible_text_in_a_field_the_converter_does_not_keep_fails_and_names_the_field_not_the_text(self):
        article = cms_article()
        article["components"][1]["html"] = "<b>隱藏的虛構文字</b>"
        self.write_csv([[SESSION, json.dumps(history(article), ensure_ascii=False)]])
        code, out, err = self.run_import()
        self.assertNotEqual(code, 0)
        self.assertIn("c-002", err)
        self.assertIn("html", err)
        self.assertNotIn("隱藏", err)
        self.assertFalse(os.path.exists(self.out_dir))

    def test_visible_text_in_an_unknown_top_level_field_fails(self):
        article = cms_article()
        article["summary"] = "虛構的摘要"
        self.write_csv([[SESSION, json.dumps(history(article), ensure_ascii=False)]])
        self.assert_rejected("summary")

    def test_a_text_field_with_the_wrong_shape_is_rejected(self):
        article = cms_article()
        article["components"][1]["text"] = 123
        self.write_csv([[SESSION, json.dumps(history(article), ensure_ascii=False)]])
        self.assert_rejected("c-002")

    def test_one_bad_row_stops_everything(self):
        bad = cms_article()
        bad["components"][0]["type"] = "poll"
        self.write_csv([[SESSION, json.dumps(history(cms_article()), ensure_ascii=False)],
                        [OTHER, json.dumps(history(bad), ensure_ascii=False)]])
        self.assert_rejected("poll")

    def test_rows_that_cannot_be_read_are_rejected(self):
        good = json.dumps(history(cms_article()), ensure_ascii=False)
        cases = {
            "history is not json": [[SESSION, "{not json"]],
            "article text is not json": [[SESSION, json.dumps([{"role": "user", "parts": [{"text": "not json"}]}])]],
            "no user turn": [[SESSION, json.dumps([{"role": "model", "parts": [{"text": "{}"}]}])]],
            "article is not an object": [[SESSION, json.dumps([{"role": "user", "parts": [{"text": "[1]"}]}])]],
            "duplicate session id": [[SESSION, good], [SESSION, good]],
            "session id that is not a safe file name": [["../escape", good]],
            "missing history column": [[SESSION]],
        }
        for name, rows in cases.items():
            with self.subTest(name):
                self.write_csv(rows)
                self.assert_rejected("import-cms")

    def test_a_missing_csv_is_rejected(self):
        self.assert_rejected("import-cms")


if __name__ == "__main__":
    unittest.main()
