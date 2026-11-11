"""showcase：給非技術讀者看的 HTML 展示報告。只含計數（模型名、組別、數量），不含文章的任何內容；從 main(argv) 進入。

表格解析成「列」再斷言，不依賴 HTML 的排版。只用虛構資料。"""
import json
import os
import unittest
from html.parser import HTMLParser

import gemini_samples
from fake_transport import FakeTransport
from fixtures import APPROACH, FAKE_KEY, Workspace, good_simplified_translation, main_only_translation, ok_response, source_article
from gate_helpers import ground_truth_for

NAME_A = "測試模型甲（虛構公司）"
NAME_B = "測試模型乙（虛構公司）"
IDS = ("9000001", "9000002", "9000003", "9000004")


class _Tables(HTMLParser):
    """HTML 裡每個有 id 的表格 → 列（每格的文字，空白合併）。"""

    def __init__(self):
        super().__init__()
        self.tables, self._current, self._row, self._cell = {}, None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._current = self.tables.setdefault(dict(attrs).get("id", ""), [])
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._current.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data.split()


def tables(html: str) -> dict:
    parser = _Tables()
    parser.feed(html)
    return parser.tables


def row_of(table: list, name: str) -> list:
    return next(row for row in table if row[0] == name)


class ShowcaseCase(Workspace):
    article_ids = IDS

    @property
    def approach_b_path(self) -> str:
        return os.path.join(self.tmp, "approach-b.json")

    def experiment(self) -> dict:
        exp = super().experiment()
        exp["approaches"].append({"config": self.approach_b_path, "runs": 1})
        return exp

    def setUp(self):
        super().setUp()
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A})
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": NAME_B})
        self.use_ground_truth(ground_truth_for(("9000001", "general"), ("9000002", "general"),
                                               ("9000003", "china-political"), ("9000004", "china-political")))

    def translate_all(self, *responses, args=()):
        code, _, err = self.run_cli("translate", *args, transport=FakeTransport(*responses))
        self.assertEqual(code, 0, err)

    def showcase(self) -> str:
        code, out, err = self.run_cli("showcase")
        self.assertEqual(code, 0, err)
        return self.output_text("showcase.html")

    def run_the_scenario(self):
        """每篇文章依序是 甲、乙 兩個模型。甲：9000002 被擋、9000004 出錯；乙：9000004 拒絕。"""
        self.translate_all(
            ok_response("9000001"), ok_response("9000001"),
            gemini_samples.prompt_blocked(), ok_response("9000002"),
            ok_response("9000003"), ok_response("9000003"),
            gemini_samples.unknown_format(), gemini_samples.refusal())


class ResultsTableTest(ShowcaseCase):
    def test_one_row_per_model_with_the_display_name_and_the_counts_of_each_result(self):
        self.run_the_scenario()
        table = tables(self.showcase())["results"]
        self.assertEqual(table[0], ["模型", "完成翻譯", "被擋（一般題材）", "被擋（中國政治題材）", "拒絕", "其他未完成"])
        self.assertEqual(row_of(table, NAME_A), [NAME_A, "50% 2/4", "50% 1/2", "0% 0/2", "0% 0/4", "25% 1/4"])
        self.assertEqual(row_of(table, NAME_B), [NAME_B, "75% 3/4", "0% 0/2", "0% 0/2", "25% 1/4", "0% 0/4"])

    def test_without_a_display_name_the_row_shows_the_model_not_the_code(self):
        self.write_json(self.approach_path, APPROACH)
        self.run_the_scenario()
        names = [row[0] for row in tables(self.showcase())["results"][1:]]
        self.assertEqual(names, [APPROACH["model"], NAME_B])

    def test_the_names_are_escaped(self):
        self.write_json(self.approach_path, {**APPROACH, "display_name": "甲<b>&乙"})
        self.run_the_scenario()
        html = self.showcase()
        self.assertIn("甲&lt;b&gt;&amp;乙", html)
        self.assertNotIn("甲<b>", html)

    def test_without_ground_truth_every_article_is_one_group(self):
        self.extra_experiment.pop("ground_truth")
        self.write_json(self.config_path, self.experiment())
        self.run_the_scenario()
        table = tables(self.showcase())["results"]
        self.assertEqual(table[0][2:4], ["被擋（全部文章）", "拒絕"])
        self.assertEqual(row_of(table, NAME_A)[2], "25% 1/4")

    def test_the_summary_lines_name_who_was_blocked_most_and_who_was_not_blocked_at_all(self):
        self.run_the_scenario()
        html = self.showcase()
        self.assertIn(f"被擋最多的是{NAME_A}：25%（1/4）", html)
        self.assertIn(f"完全沒被擋：{NAME_B}", html)
        self.assertIn(f"其他原因沒有拿到譯文最多的是{NAME_A}：25%（1/4）", html)

    def test_when_nothing_was_blocked_it_says_so(self):
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(2)])
        html = self.showcase()
        self.assertIn("沒有任何模型被擋", html)
        self.assertNotIn("被擋最多的是", html)


class RoundingTest(Workspace):
    article_ids = ("9000001", "9000002", "9000003")

    def test_percentages_are_rounded_to_the_nearest_whole_number(self):
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A})
        code, _, err = self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked(), gemini_samples.prompt_blocked(),
                                                                         ok_response("9000003")))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("showcase")[0], 0)
        row = row_of(tables(self.output_text("showcase.html"))["results"], NAME_A)
        self.assertEqual(row[1:], ["33% 1/3", "67% 2/3", "0% 0/3", "0% 0/3"])   # 2/3 是 67%，不是 66%


class ProgressTest(ShowcaseCase):
    def test_an_unfinished_experiment_says_so_and_a_finished_one_does_not(self):
        self.translate_all(ok_response("9000001"), ok_response("9000001"), ok_response("9000002"), args=("--limit", "3"))
        html = self.showcase()
        self.assertIn("已完成 3／8 個 Run", html)
        self.assertIn("仍在進行", html)
        self.assertNotIn("已全部跑完", html)
        self.translate_all(ok_response("9000002"), *[ok_response(i) for i in IDS[2:] for _ in range(2)])
        html = self.showcase()
        self.assertIn("已全部跑完：8 個 Run", html)
        self.assertNotIn("仍在進行", html)

    def test_runs_that_still_need_a_redo_are_mentioned_and_otherwise_not(self):
        self.run_the_scenario()   # 甲有一個 error
        self.assertIn("尚待重做", self.showcase())
        self.run_cli("translate", "--redo-errors", transport=FakeTransport(ok_response("9000004")))
        self.assertNotIn("尚待重做", self.showcase())


class DetailTest(ShowcaseCase):
    def test_the_detail_table_has_every_result_class_per_model_direction_and_input_version(self):
        self.run_the_scenario()
        details = tables(self.showcase())["details"]
        self.assertEqual(details[0], ["模型", "方向", "輸入版本", "Run 數", "成功", "被擋", "拒絕", "空內容", "輸出格式壞", "截斷", "出錯"])
        self.assertEqual(row_of(details, NAME_A), [NAME_A, "繁→英", "完整文章", "4", "2", "1", "0", "0", "0", "0", "1"])
        self.assertEqual(row_of(details, NAME_B), [NAME_B, "繁→英", "完整文章", "4", "3", "0", "1", "0", "0", "0", "0"])


class RepeatedRunsTest(Workspace):
    """甲每篇每個方向跑 2 次，另有只送標題與正文的版本；主表只看完整文章的第 1 次，重複與只送正文放在另一張表。"""
    article_ids = ("9000001", "9000002")
    runs = 2
    input_variants = ["full", "main-only"]

    def setUp(self):
        super().setUp()
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A})
        self.use_ground_truth(ground_truth_for(("9000001", "general"), ("9000002", "general")))

    def main_only(self, article_id):
        return gemini_samples.ok(json.dumps(main_only_translation(article_id), ensure_ascii=False))

    def prepare(self):
        # 9000001：完整文章第 1 次被擋、第 2 次成功，只送正文都成功；9000002 全部成功
        responses = [gemini_samples.prompt_blocked(), ok_response("9000001"), self.main_only("9000001"), self.main_only("9000001"),
                     ok_response("9000002"), ok_response("9000002"), self.main_only("9000002"), self.main_only("9000002")]
        code, _, err = self.run_cli("translate", transport=FakeTransport(*responses))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("showcase")[0], 0)
        return self.output_text("showcase.html")

    def test_the_main_table_counts_only_the_first_run_of_the_full_article(self):
        row = row_of(tables(self.prepare())["results"], NAME_A)
        self.assertEqual(row[1:], ["50% 1/2", "50% 1/2", "0% 0/2", "0% 0/2"])

    def test_the_repeats_and_the_title_and_body_only_version_have_their_own_table(self):
        table = tables(self.prepare())["stability"]
        self.assertEqual(table[0], ["模型", "每篇每個方向跑幾次", "完整文章：被擋的 Run", "完整文章：至少被擋一次的文章", "只送標題與正文：被擋的 Run"])
        self.assertEqual(row_of(table, NAME_A), [NAME_A, "2", "25% 1/4", "50% 1/2", "0% 0/4"])

    def test_the_detail_table_has_a_row_for_each_input_version(self):
        details = tables(self.prepare())["details"]
        self.assertEqual([row[2:5] for row in details[1:]], [["完整文章", "4", "3"], ["只送標題與正文", "4", "4"]])


class NoStabilityTableTest(ShowcaseCase):
    def test_a_model_run_once_on_the_full_article_has_no_such_table(self):
        self.run_the_scenario()
        self.assertNotIn("stability", tables(self.showcase()))


class SectionsTest(ShowcaseCase):
    def test_it_says_what_was_tested_and_how_many_articles_are_in_each_group(self):
        self.run_the_scenario()
        html = self.showcase()
        self.assertIn("一般題材 2 篇", html)
        self.assertIn("中國政治題材 2 篇", html)
        self.assertIn(NAME_A, html)

    def test_a_baseline_is_listed_with_its_display_name_and_is_not_a_row_of_the_results(self):
        self.extra_experiment["baselines"] = [{"id": "e", "dir": os.path.join(self.tmp, "baseline"), "direction": "trad-to-simplified",
                                               "display_name": "現行服務（虛構）"}]
        self.directions = ["trad-to-en", "trad-to-simplified"]
        self.write_json(self.config_path, self.experiment())
        for article_id in IDS:
            self.write_baseline(os.path.join(self.tmp, "baseline"), article_id, good_simplified_translation(article_id))
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(4)])   # 兩個模型 × 兩個方向
        self.assertEqual(self.run_cli("import-baseline")[0], 0)
        html = self.showcase()
        self.assertIn("現行服務（虛構）", html)
        self.assertEqual([row[0] for row in tables(html)["results"][1:]], [NAME_A, NAME_B])
        self.assertIn("已全部跑完：16 個 Run", html)   # 匯入的 baseline（4 個）不算翻譯的進度
        self.assertEqual(len(tables(html)["details"]) - 1, 4)   # 細節表也只有兩個模型 × 兩個方向

    def test_without_a_judge_it_says_the_softening_rate_and_the_quality_are_not_measured_yet(self):
        self.run_the_scenario()
        self.assertIn("沒有設定 Judge", self.showcase())

    def test_with_a_judge_it_points_to_the_report_instead(self):
        self.use_judging()
        self.run_the_scenario()
        html = self.showcase()
        self.assertNotIn("沒有設定 Judge", html)
        self.assertIn("report.md", html)


class SafetyTest(ShowcaseCase):
    def test_the_page_has_counts_only_no_article_text_no_ids_and_no_key(self):
        self.run_the_scenario()
        html = self.showcase()
        for secret in (source_article("9000001")["title"], "9000001", "9000004", FAKE_KEY):
            self.assertNotIn(secret, html)

    def test_the_page_loads_nothing_from_the_network(self):
        self.run_the_scenario()
        html = self.showcase()
        for needle in ("http://", "https://", "<script", "@import"):
            self.assertNotIn(needle, html)

    def test_no_runs_is_a_clean_failure_that_writes_nothing(self):
        code, out, err = self.run_cli("showcase")
        self.assertNotEqual(code, 0)
        self.assertIn("沒有 Run 紀錄", err)
        self.assertEqual(out, "")
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase.html")))


class DisplayNameConfigTest(ShowcaseCase):
    def test_a_display_name_does_not_change_the_config_hash(self):
        self.write_json(self.approach_path, APPROACH)
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b"})
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(2)])
        plain = {r["approach"]: r["config_hash"] for r in self.read_runs()}
        os.remove(os.path.join(self.out_dir, "runs.jsonl"))
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A})
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": NAME_B})
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(2)])
        self.assertEqual({r["approach"]: r["config_hash"] for r in self.read_runs()}, plain)

    def test_two_models_with_the_same_name_are_refused(self):
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": NAME_A})
        self.assert_fails_cleanly("顯示名稱重覆")

    def assert_showcase_refuses_the_names(self):
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(2)])   # 翻譯本身不在乎名稱
        code, out, err = self.run_cli("showcase")
        self.assertNotEqual(code, 0)
        self.assertIn("顯示名稱重覆", err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase.html")))

    def test_a_name_that_is_another_models_fallback_is_refused_by_showcase(self):
        # 甲沒有顯示名稱時，列出來的名字是它的 model；乙取了同一個字串，兩列會分不出來
        self.write_json(self.approach_path, APPROACH)
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": APPROACH["model"]})
        self.assert_showcase_refuses_the_names()

    def test_two_models_without_names_and_with_the_same_model_are_refused_by_showcase(self):
        self.write_json(self.approach_path, APPROACH)
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b"})
        self.assert_showcase_refuses_the_names()

    def test_a_blank_name_is_refused(self):
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": "  "})
        self.assert_fails_cleanly("display_name")


if __name__ == "__main__":
    unittest.main()
