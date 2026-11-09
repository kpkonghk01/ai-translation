"""與 baseline 比較：每個 Approach 的 MQM 分數對 baseline 的分數。從 main(argv) 進入：translate → import-baseline → judge → check → report。
預期的數字都是手算的：Main 的原文字數（不計空白）每篇是 title 11 + highlight 14 + paragraph 19 = 44；Peripheral 是圖說 9 + tags 4 = 13；
Judge 對 b 的 highlight 提出一個 major 的 Accuracy Finding（罰分 5）。"""
import json
import os
import shutil
from pathlib import Path

import gemini_samples
from fake_transport import FakeTransport, FunctionTransport
from fixtures import APPROACH, Workspace, good_simplified_translation, good_translation, main_only_translation, ok_response, simplified_response, source_article
from judge_helpers import NO_PROBLEMS, finding, user_payload

B = {**APPROACH, "id": "b", "model": "gemini-3.1-other"}
B_HIGHLIGHT = "Renovation delayed by six months, over budget."   # b 的 highlight 譯文：Judge 對它提出 Finding
PER_1000_ONE_ARTICLE = "113.6"   # 5 ÷ 44 × 1000


def b_response(article_id="9000001", highlight=B_HIGHLIGHT):
    translation = good_translation(source_article(article_id))
    next(c for c in translation["components"] if c["type"] == "highlight")["text"] = highlight
    return gemini_samples.ok(json.dumps(translation, ensure_ascii=False))


def judge_b_transport():
    """譯文是 B_HIGHLIGHT 的 highlight 兩次都被提出 major 的 Accuracy Finding；其餘沒有問題。"""
    def reply(request):
        component = user_payload(request)["component"]
        item = {"findings": [finding(severity="major", source_span="預算超支", target_span="over budget")], "source_defects": []} \
            if component["translation"] == B_HIGHLIGHT else NO_PROBLEMS
        return gemini_samples.ok(json.dumps(item, ensure_ascii=False))
    return FunctionTransport(reply)


class CompareCase(Workspace):
    article_ids = ("9000001", "9000002")

    def setUp(self):
        super().setUp()
        for article_id in self.article_ids:
            self.write_json(os.path.join(self.articles_dir, f"{article_id}.json"), source_article(article_id))
        self.b_path = os.path.join(self.tmp, "approach-b.json")
        self.write_json(self.b_path, B)
        self.extra_experiment["approaches"] = [{"config": self.approach_path, "runs": 1}, {"config": self.b_path, "runs": 1}]
        self.extra_experiment["comparison"] = {"baseline": "a"}
        self.use_judging()

    def translate(self, *responses):
        """依序的回應：每篇文章先 a 再 b。"""
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(*responses))[0], 0)

    def world(self, *responses):
        self.translate(*(responses or (ok_response("9000001"), b_response("9000001"), ok_response("9000002"), b_response("9000002"))))
        return self.finish()

    def finish(self, transport=None, *judge_args):
        code, _, err = self.run_cli("judge", *judge_args, transport=transport or judge_b_transport())
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("check")[0], 0)
        code, _, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        return self.report()

    def report(self) -> str:
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def section(report: str, heading: str) -> str:
        return report.split(f"### {heading}")[1].split("\n#")[0]


class QualityComparisonTest(CompareCase):
    HEADING = "b × trad-to-en 對 a（質素 baseline）× Judge j1"

    def test_each_approach_is_compared_with_the_quality_baseline_over_the_articles_both_have(self):
        section = self.section(self.world(), self.HEADING)
        self.assertIn("比較的文章：2 篇（9000001、9000002）", section)
        self.assertIn(f"- Main：每千字原文的罰分（只計一致的 Finding／連 uncertain 一起計）：b {PER_1000_ONE_ARTICLE}／{PER_1000_ONE_ARTICLE}、a 0.0／0.0、"
                      f"差 +{PER_1000_ONE_ARTICLE}／+{PER_1000_ONE_ARTICLE}（原文 88 字）", section)
        self.assertIn("- Main：平均每個 Run 的總罰分：b 10.0／10.0、a 0.0／0.0", section)
        self.assertIn("Peripheral：每千字原文的罰分（只計一致的 Finding／連 uncertain 一起計）：b 0.0／0.0、a 0.0／0.0、差 +0.0／+0.0（原文 26 字）", section)

    def test_only_full_runs_are_compared_so_a_main_only_run_does_not_water_down_the_score(self):
        self.extra_experiment["approaches"] = [{"config": self.approach_path, "runs": 1}, {"config": self.b_path, "runs": 1, "input_variants": ["full", "main-only"]}]
        self.write_json(self.config_path, self.experiment())
        main_only = lambda article_id: gemini_samples.ok(json.dumps(main_only_translation(article_id), ensure_ascii=False))   # noqa: E731
        report = self.world(ok_response("9000001"), b_response("9000001"), main_only("9000001"),
                            ok_response("9000002"), b_response("9000002"), main_only("9000002"))
        self.assertIn("- Main：平均每個 Run 的總罰分：b 10.0／10.0、a 0.0／0.0", self.section(report, self.HEADING))
        self.assertNotIn("main-only）× Judge j1\n\n- 比較的文章", report)

    def test_the_baseline_is_not_compared_with_itself(self):
        report = self.world()
        self.assertNotIn("a × trad-to-en 對 a", report)
        self.assertEqual(report.count("## 與 baseline 比較"), 1)

    def test_an_article_the_baseline_was_blocked_on_is_left_out_and_its_id_is_listed(self):
        report = self.world(ok_response("9000001"), b_response("9000001"), gemini_samples.prompt_blocked(), b_response("9000002"))
        section = self.section(report, self.HEADING)
        self.assertIn("比較的文章：1 篇（9000001）", section)
        self.assertIn("a 沒有全部 ok 的 Run 1 篇（9000002）", section)
        self.assertIn("（原文 44 字）", section)   # b 在 9000002 的分數沒有進來

    def test_an_article_the_approach_has_no_translation_for_is_left_out_and_not_counted_as_a_perfect_score(self):
        section = self.section(self.world(ok_response("9000001"), b_response("9000001"), ok_response("9000002"), gemini_samples.prompt_blocked()), self.HEADING)
        self.assertIn("比較的文章：1 篇（9000001）", section)
        self.assertIn("b 沒有 ok 的 Run 1 篇（9000002）", section)

    def test_an_article_that_is_not_judged_yet_is_left_out(self):
        self.translate(ok_response("9000001"), b_response("9000001"), ok_response("9000002"), b_response("9000002"))
        section = self.section(self.finish(None, "--article", "9000001"), self.HEADING)
        self.assertIn("比較的文章：1 篇（9000001）", section)
        self.assertIn("尚未評完 1 篇（9000002）", section)

    def test_with_several_runs_each_article_takes_the_mean_of_its_ok_runs(self):
        self.extra_experiment["approaches"] = [{"config": self.approach_path, "runs": 1}, {"config": self.b_path, "runs": 2}]
        self.write_json(self.config_path, self.experiment())
        # b 的第 1 個 Run 有 Finding，第 2 個沒有（譯文與 a 的一樣）：平均罰分 2.5
        section = self.section(self.world(ok_response("9000001"), b_response("9000001"), ok_response("9000001"),
                                          ok_response("9000002"), b_response("9000002"), ok_response("9000002")), self.HEADING)
        self.assertIn("- Main：平均每個 Run 的總罰分：b 5.0／5.0、a 0.0／0.0", section)   # 兩篇各 2.5

    def test_runs_of_the_baseline_must_all_be_ok_for_the_article_to_count(self):
        self.extra_experiment["approaches"] = [{"config": self.approach_path, "runs": 2}, {"config": self.b_path, "runs": 1}]
        self.write_json(self.config_path, self.experiment())
        report = self.world(ok_response("9000001"), ok_response("9000001"), b_response("9000001"),
                            ok_response("9000002"), gemini_samples.prompt_blocked(), b_response("9000002"))
        section = self.section(report, self.HEADING)
        self.assertIn("比較的文章：1 篇（9000001）", section)   # 9000002 的第 2 個 Run 被 Block：baseline 不是所有 Run 都 ok

    def test_a_baseline_that_has_fewer_runs_recorded_than_planned_does_not_count_the_article(self):
        self.extra_experiment["approaches"] = [{"config": self.approach_path, "runs": 2}, {"config": self.b_path, "runs": 1}]
        self.write_json(self.config_path, self.experiment())
        self.translate(ok_response("9000001"), ok_response("9000001"), b_response("9000001"),
                       ok_response("9000002"), ok_response("9000002"), b_response("9000002"))
        runs_path = os.path.join(self.out_dir, "runs.jsonl")
        lines = Path(runs_path).read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if not (json.loads(line)["article_id"] == "9000002" and json.loads(line)["approach"] == "a" and json.loads(line)["run_index"] == 2)]
        self.assertEqual(len(kept), len(lines) - 1)
        with open(runs_path, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n")   # 9000002 的 a 只剩第 1 個 Run：計畫是 2 個
        section = self.section(self.finish(), self.HEADING)
        self.assertIn("比較的文章：1 篇（9000001）", section)
        self.assertIn("a 沒有全部 ok 的 Run 1 篇（9000002）", section)

    def test_the_score_follows_the_weights_without_judging_again(self):
        self.world()
        self.extra_experiment["weights"] = {"major": 10}
        self.write_json(self.config_path, self.experiment())
        self.assertEqual(self.run_cli("report")[0], 0)
        self.assertIn("b 20.0／20.0、a 0.0／0.0", self.section(self.report(), self.HEADING))

    def test_there_is_no_comparison_section_without_a_comparison_config_or_without_judging(self):
        self.extra_experiment.pop("comparison")
        self.write_json(self.config_path, self.experiment())
        self.assertNotIn("## 與 baseline 比較", self.world())
        self.extra_experiment["comparison"] = {"baseline": "a"}
        self.write_json(self.config_path, self.experiment())
        shutil.rmtree(self.data_dir)
        self.translate(ok_response("9000001"), b_response("9000001"), ok_response("9000002"), b_response("9000002"))
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)   # 還沒有 Judge 紀錄
        self.assertNotIn("## 與 baseline 比較", self.report())


class SimplifiedBaselineTest(CompareCase):
    article_ids = ("9000001",)

    def setUp(self):
        super().setUp()
        self.baseline_dir = os.path.join(self.tmp, "baseline-e")
        self.write_baseline(self.baseline_dir, "9000001", good_simplified_translation("9000001"))
        self.extra_experiment["baselines"] = [{"id": "e", "dir": self.baseline_dir, "direction": "trad-to-simplified"}]
        self.extra_experiment["directions"] = ["trad-to-en", "trad-to-simplified"]
        self.extra_experiment["comparison"] = {"baseline": "a", "also_against": {"trad-to-simplified": ["e"]}}
        self.write_json(self.config_path, self.experiment())

    def simplified_world(self):
        b_simplified = good_simplified_translation("9000001")
        next(c for c in b_simplified["components"] if c["type"] == "highlight")["text"] = "翻新工程延误六个月，预算超支。"
        # 順序：a 繁→英、a 繁→簡、b 繁→英、b 繁→簡
        self.translate(ok_response(), simplified_response(), b_response(), gemini_samples.ok(json.dumps(b_simplified, ensure_ascii=False)))
        self.assertEqual(self.run_cli("import-baseline")[0], 0)

        def reply(request):
            component = user_payload(request)["component"]
            flagged = component["translation"] in (B_HIGHLIGHT, "翻新工程延误六个月，预算超支。")
            item = {"findings": [finding(severity="major", source_span="預算超支", target_span="over budget" if "over" in component["translation"] else "预算超支")],
                    "source_defects": []} if flagged else NO_PROBLEMS
            return gemini_samples.ok(json.dumps(item, ensure_ascii=False))
        return self.finish(FunctionTransport(reply))

    def test_simplified_is_also_compared_with_service_e_but_english_is_not(self):
        report = self.simplified_world()
        self.assertIn("### b × trad-to-simplified 對 a（質素 baseline）× Judge j1", report)
        self.assertIn("### b × trad-to-simplified 對 e（baseline）× Judge j1", report)
        self.assertNotIn("trad-to-en 對 e", report)
        section = self.section(report, "b × trad-to-simplified 對 e（baseline）× Judge j1")
        self.assertIn("比較的文章：1 篇（9000001）", section)
        self.assertIn("：b 113.6／113.6、e 0.0／0.0、差 +113.6／+113.6（原文 44 字）", section)

    def test_the_service_is_compared_with_the_quality_baseline_too_and_never_with_itself(self):
        report = self.simplified_world()
        self.assertIn("### e × trad-to-simplified 對 a（質素 baseline）× Judge j1", report)
        self.assertNotIn("e × trad-to-simplified 對 e", report)
        self.assertNotIn("### a × trad-to-simplified 對 e", report)   # 質素 baseline 不是被比的對象；E 對 A 已經列了
        self.assertNotIn("### e × trad-to-en", report)   # E 只有繁→簡

    def test_a_blocked_service_run_leaves_the_article_out_of_the_service_comparison(self):
        self.translate(ok_response(), simplified_response(), b_response(), simplified_response())
        self.assertEqual(self.run_cli("import-baseline")[0], 0)
        runs_path = os.path.join(self.out_dir, "runs.jsonl")
        runs = [json.loads(line) for line in Path(runs_path).read_text(encoding="utf-8").splitlines()]
        for run in runs:
            if run["approach"] == "e":
                run.update(outcome="block", reason="prompt:FICTIONAL", translation=None)
        with open(runs_path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(run, ensure_ascii=False) + "\n" for run in runs))
        report = self.finish()
        section = self.section(report, "b × trad-to-simplified 對 e（baseline）× Judge j1")
        self.assertIn("e 沒有全部 ok 的 Run 1 篇（9000001）", section)
        self.assertIn("沒有可以比較的文章", section)


class ConfigTest(CompareCase):
    def assert_rejected(self, needle):
        code, out, err = self.run_cli("report", transport=None)
        self.assertNotEqual(code, 0)
        self.assertIn(needle, err)

    def test_a_baseline_or_direction_that_does_not_exist_is_rejected(self):
        for comparison, needle in (({"baseline": "zz"}, "comparison.baseline 指向不存在"),
                                   ({"baseline": "a", "also_against": {"trad-to-en": ["zz"]}}, "comparison.also_against.trad-to-en 指向不存在"),
                                   ({"baseline": "a", "also_against": {"trad-to-simplified": ["a"]}}, "Direction 不在實驗內")):
            with self.subTest(needle):
                self.extra_experiment["comparison"] = comparison
                self.write_json(self.config_path, self.experiment())
                self.assert_rejected(needle)
