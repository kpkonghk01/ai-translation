"""showcase --judged-in / --calibrated-in：展示頁的「淡化」一節。只放計數與白話說明，沒有文章內容；從 main(argv) 進入。

--judged-in：Judge 評核過的實驗設定（通常是抽樣實驗）；--calibrated-in：Judge 校準過的實驗設定。這裡的測試用同一個虛構實驗當兩者
（兩篇文章：9000001 一般題材有 2 個敏感句、9000002 中國政治題材有 1 個）。只用虛構資料。"""
import json
import os
import re
import shutil
import unittest

import gemini_samples
from calibration_helpers import FACT_DELAY, CalibrationCase
from fake_transport import FakeTransport
from fixtures import good_simplified_translation, main_only_translation, ok_response, simplified_response
from judge_helpers import fact_output, fact_transport
from test_showcase import tables

NAME = "測試模型甲（虛構公司）"
WEAK = {"verdict": "Weakened", "type": "d", "quote": "late", "reason": "虛構的理由：細節被概括"}


class FadeCase(CalibrationCase):
    def setUp(self):
        super().setUp()
        self.write_json(self.approach_path, {**json.load(open(self.approach_path, encoding="utf-8")), "display_name": NAME})
        self.translate_both()

    def judge_facts(self, outputs=None):
        outputs = {"9000001-001": lambda p: fact_output(p, {FACT_DELAY: WEAK})} if outputs is None else outputs
        code, _, err = self.run_cli("judge", "--task", "fact", transport=fact_transport(outputs))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("check")[0], 0)

    def showcase(self, *extra) -> str:
        code, _, err = self.run_cli("showcase", "--judged-in", self.config_path, *extra)
        self.assertEqual(code, 0, err)
        return self.output_text("showcase.html")

    def fade(self, *extra) -> list:
        return tables(self.showcase(*extra))["fade"]


class FadeTableTest(FadeCase):
    def test_one_row_per_model_and_one_column_per_direction_and_group(self):
        self.judge_facts()
        table = self.fade()
        self.assertEqual(table[0], ["模型", "繁→英・一般題材", "繁→英・中國政治題材"])
        self.assertEqual([row[0] for row in table[1:]], [NAME])

    def test_a_cell_has_the_weakened_sentences_over_the_judged_ones_and_the_upper_bound(self):
        self.judge_facts()   # 一般題材 2 個敏感句裡，001 的那一句被判淡化；中國政治題材 1 句都沒事
        row = tables(self.showcase())["fade"][1]
        self.assertRegex(row[1], r"^50% 1/2 95% 上限 \d+%$")
        self.assertEqual(row[2], "0% 0/1 95% 上限 95%")   # 0/1 的單尾 95% 上限是 1 − 0.05 = 95%

    def test_a_group_with_no_judged_sentence_says_there_is_no_result_instead_of_zero_percent(self):
        shutil.rmtree(self.data_dir)   # 重來：中國政治題材那篇被擋，沒有譯文可以核對
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), gemini_samples.prompt_blocked()))[0], 0)
        self.judge_facts()
        row = tables(self.showcase())["fade"][1]
        self.assertEqual(row[2], "沒有結果")
        self.assertRegex(row[1], r"^\d+% \d/\d 95% 上限 \d+%$")


class StatusTest(FadeCase):
    def test_the_number_of_must_see_items_is_the_one_the_review_list_has(self):
        self.judge_facts()
        out = self.run_cli("review")[1]
        must = re.search(r"必看 (\d+) 項", out).group(1)
        self.assertIn(f"必看項目 {must} 項，已有決定 0 項", self.showcase())

    def test_it_says_it_is_preliminary_who_judged_how_many_articles_and_how_much_was_reviewed(self):
        self.judge_facts()
        html = self.showcase()
        self.assertIn("淡化：譯文有沒有把內容寫輕（初步）", html)
        self.assertIn("尚未經人審", html)
        self.assertIn("gemini-3.1-pro", html)   # 主 Judge：沒有顯示名稱就用 model
        self.assertIn("評核了 2 篇文章（一般題材 1 篇、中國政治題材 1 篇）", html)
        self.assertRegex(html, r"必看項目 \d+ 項，已有決定 0 項")

    def test_a_review_decision_changes_the_numbers_and_the_count_of_decisions(self):
        self.judge_facts()
        self.assertEqual(self.run_cli("review", "--approach", "a", "--status", "failed")[0], 0)
        focus = self.output_text("review-focus.md")
        key, input_hash = re.search(r"key：`(fact-[0-9a-f]{16})`；輸入 hash：`([0-9a-f]{16})`", focus).groups()
        self.write_json(os.path.join(self.out_dir, "review-decisions.json"), {"decisions": [
            {"key": key, "input_hash": input_hash, "action": "override", "verdict": "Present", "reviewer": "虛構審閱者", "reviewed_on": "2026-11-12"}]})
        html = self.showcase()
        self.assertEqual(tables(html)["fade"][1][1].split(" 95%")[0], "0% 0/2")
        self.assertRegex(html, r"已有決定 1 項")


class StaleDecisionTest(FadeCase):
    def test_a_decision_whose_input_has_changed_stops_it_like_the_report_does(self):
        self.judge_facts()
        self.run_cli("review", "--approach", "a", "--status", "failed")
        key = re.search(r"key：`(fact-[0-9a-f]{16})`", self.output_text("review-focus.md")).group(1)
        self.write_json(os.path.join(self.out_dir, "review-decisions.json"), {"decisions": [
            {"key": key, "input_hash": "0" * 16, "action": "accept", "reviewer": "虛構審閱者", "reviewed_on": "2026-11-12"}]})
        code, _, err = self.run_cli("showcase", "--judged-in", self.config_path)
        self.assertNotEqual(code, 0)
        self.assertIn(key, err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase.html")))


class NotYetSectionTest(FadeCase):
    def test_the_last_section_points_at_the_fade_section_and_says_quality_was_not_done(self):
        self.judge_facts()
        html = self.showcase()
        self.assertIn("淡化率是初步結果，見上一節。譯文質素（逐段評分）這一批沒有做。", html)
        self.assertNotIn("沒有設定 Judge", html)


class JudgeAccuracyTest(FadeCase):
    def test_without_a_calibration_it_says_the_judges_accuracy_is_not_measured(self):
        self.judge_facts()
        self.assertIn("Judge 的準確度還沒有量度", self.showcase())

    def test_with_a_calibration_it_says_how_many_known_weakenings_the_judge_caught(self):
        self.judge_facts()
        self.calibrate_j1()
        html = self.showcase("--calibrated-in", self.config_path)
        self.assertIn("已知答案裡被淡化的事實，Judge 抓到 2/3", html)   # 校準情境：失敗的事實 recall 2/3
        self.assertIn("沒淡化卻被判成淡化的 0/1", html)
        self.assertNotIn("還沒有量度", html)

    def test_the_text_says_the_known_answers_are_hand_written_so_the_figure_is_an_upper_bound(self):
        self.judge_facts()
        self.calibrate_j1()
        self.assertIn("人手寫", self.showcase("--calibrated-in", self.config_path))


class ExplanationTest(FadeCase):
    def test_how_to_read_it_says_what_weakening_is_and_what_is_left_out_of_the_denominator(self):
        self.judge_facts()
        html = self.showcase()
        self.assertIn("一句只要有一條要保留的事實被寫輕", html)
        self.assertIn("被擋或沒有輸出的文章不在分母", html)

    def test_each_kind_of_weakening_has_an_invented_example_marked_as_such(self):
        self.judge_facts()
        html = self.showcase()
        self.assertIn("以下是虛構的例子，只用來說明", html)
        for label in ("施害者或行為主體被拿掉", "用詞被改得委婉或中性", "具體細節被刪掉或概括", "對原文斷言的事加上限定"):
            self.assertIn(label, html)
        self.assertEqual(html.count('class="illustration"'), 4)


class OnlyTheFullArticleTest(CalibrationCase):
    """評核的實驗也有只送標題與正文的版本：淡化率只看完整文章。"""
    input_variants = ["full", "main-only"]

    def test_the_main_only_runs_are_not_in_the_table(self):
        main_only = lambda i: gemini_samples.ok(json.dumps(main_only_translation(i), ensure_ascii=False))  # noqa: E731
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), main_only("9000001"),
                                                                           ok_response("9000002"), main_only("9000002")))[0], 0)
        # 一般題材 001 的 Judge 請求依序：完整文章的兩次判淡化，只送正文的兩次判 Present
        weak = lambda p: fact_output(p, {FACT_DELAY: WEAK})  # noqa: E731
        outputs = {("9000001-001", 1): weak, ("9000001-001", 2): weak}
        self.assertEqual(self.run_cli("judge", "--task", "fact", transport=fact_transport(outputs))[0], 0)
        self.assertEqual(self.run_cli("check")[0], 0)
        code, _, err = self.run_cli("showcase", "--judged-in", self.config_path)
        self.assertEqual(code, 0, err)
        general = tables(self.output_text("showcase.html"))["fade"][1][1]
        self.assertTrue(general.startswith("50% 1/2"), general)   # 完整文章 1/2；只送正文是 0/2，不能蓋掉它


class BaselineRowTest(CalibrationCase):
    """對照的現行服務只做繁→簡：繁→英的格是「不適用」，不是沒有結果。"""
    directions = ["trad-to-en", "trad-to-simplified"]

    def test_a_baseline_with_one_direction_has_a_dash_in_the_other(self):
        baseline_dir = os.path.join(self.tmp, "baseline")
        for article_id in self.article_ids:
            self.write_baseline(baseline_dir, article_id, good_simplified_translation(article_id))
        self.use_baseline(baseline_dir)
        self.extra_experiment["baselines"][0]["display_name"] = "現行服務（虛構）"
        self.write_json(self.config_path, self.experiment())
        self.assertEqual(self.run_cli("import-baseline")[0], 0)
        responses = [r for i in self.article_ids for r in (ok_response(i), simplified_response(i))]
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(*responses))[0], 0)
        self.assertEqual(self.run_cli("judge", "--task", "fact", transport=fact_transport({}))[0], 0)
        self.assertEqual(self.run_cli("check")[0], 0)
        code, _, err = self.run_cli("showcase", "--judged-in", self.config_path)
        self.assertEqual(code, 0, err)
        table = tables(self.output_text("showcase.html"))["fade"]
        self.assertEqual(table[0][1:], ["繁→英・一般題材", "繁→英・中國政治題材", "繁→簡・一般題材", "繁→簡・中國政治題材"])
        baseline = next(row for row in table if row[0] == "現行服務（虛構）")
        self.assertEqual(baseline[1:3], ["—", "—"])
        self.assertTrue(all(cell.startswith("0% 0/") for cell in baseline[3:]), baseline)


class SafetyTest(FadeCase):
    def test_no_article_text_id_or_judge_output_is_on_the_page(self):
        self.judge_facts()
        html = self.showcase()
        for text in ("虛構市政廳翻新工程延誤", "The renovation", "9000001", "9000002", "虛構的理由", "細節被概括"):
            self.assertNotIn(text, html)

    def test_without_the_option_there_is_no_fade_section_and_nothing_changes(self):
        self.judge_facts()
        code, _, err = self.run_cli("showcase")
        self.assertEqual(code, 0, err)
        self.assertNotIn("淡化：", self.output_text("showcase.html"))


class RefusalsTest(FadeCase):
    def test_a_judged_experiment_that_does_not_exist_stops_without_writing_a_page(self):
        code, _, err = self.run_cli("showcase", "--judged-in", os.path.join(self.tmp, "nothing.json"))
        self.assertNotEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase.html")))

    def test_the_judged_experiment_must_have_a_judge(self):
        self.judge_facts()
        self.write_json(self.config_path, {k: v for k, v in self.experiment().items() if k != "judging"})
        code, _, err = self.run_cli("showcase", "--judged-in", self.config_path)
        self.assertNotEqual(code, 0)
        self.assertIn("沒有設定 Judge", err)

    def test_a_judged_experiment_with_no_judge_results_yet_stops_and_says_so(self):
        code, _, err = self.run_cli("showcase", "--judged-in", self.config_path)
        self.assertNotEqual(code, 0)
        self.assertIn("還沒有 Judge 的事實核對紀錄", err)

    def test_calibrated_in_without_judged_in_is_refused(self):
        code, _, err = self.run_cli("showcase", "--calibrated-in", self.config_path)
        self.assertNotEqual(code, 0)
        self.assertIn("--calibrated-in 要和 --judged-in 一起用", err)


if __name__ == "__main__":
    unittest.main()
