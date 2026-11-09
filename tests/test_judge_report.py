"""Judge 的一致性、Source defect、計分，以及報告的 Judge 章節。分數由程式按規則計算，Judge 只給 Finding。
預期的數字都是手算的：Main component 的原文字數（不計空白）是 title 11 + highlight 14 + paragraph 19 = 44；
Peripheral 是圖說 9 + tags 4 = 13。"""
import json
import os
import shutil
import unittest

import gemini_samples
from fake_transport import FakeTransport
from fixtures import JUDGE, good_simplified_translation, good_translation, ok_response, source_article
from judge_helpers import finding, judge_transport
from test_judge import JudgeCase

PENALTY_PER_1000 = "113.6"   # 5 ÷ 44 × 1000


class ReportCase(JudgeCase):
    def run_all(self, outputs=None, *, judge_args=(), transport=None):
        self.judge(transport or judge_transport(outputs), *judge_args)
        self.assertEqual(self.run_cli("check")[0], 0)
        code, _, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        return self.report_text()

    def report_text(self):
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def section(text, heading="### a × trad-to-en × Judge j1（primary）"):
        return text.split(heading)[1].split("\n#")[0]


class ConsistencyTest(ReportCase):
    def test_a_finding_both_calls_report_is_consistent_and_counts_in_both_scores(self):
        text = self.run_all({"9000001-001": {"findings": [finding()], "source_defects": []}})
        main = self.section(text)
        self.assertIn(f"- Main：總罰分 5（只計一致的 Finding）／5（連 uncertain 一起計）；每千字原文的罰分 {PENALTY_PER_1000}／{PENALTY_PER_1000}（原文 44 字）", main)
        self.assertIn("- Peripheral：總罰分 0（只計一致的 Finding）／0（連 uncertain 一起計）；每千字原文的罰分 0.0／0.0（原文 13 字）", main)

    def test_a_finding_only_one_call_reports_is_uncertain_and_only_counts_in_the_second_score(self):
        main = self.section(self.run_all({("9000001-001", 1): {"findings": [finding()], "source_defects": []}}))
        self.assertIn("- Main：總罰分 0（只計一致的 Finding）／5（連 uncertain 一起計）", main)

    def test_two_calls_that_overlap_on_the_same_dimension_are_one_finding_and_the_higher_severity_wins(self):
        outputs = {("9000001-001", 1): {"findings": [finding(severity="major", target_span="over budget")], "source_defects": []},
                   ("9000001-001", 2): {"findings": [finding(severity="critical", target_span="late and over")], "source_defects": []}}
        main = self.section(self.run_all(outputs))
        self.assertIn("- Main：總罰分 25（只計一致的 Finding）／25（連 uncertain 一起計）", main)   # 重疊的片段是同一個 Finding，取 critical
        self.assertIn("- Severity 兩次不一致的 Finding：1", main)

    def test_overlap_needs_the_same_dimension_and_spans_that_really_overlap(self):
        outputs = {("9000001-001", 1): {"findings": [finding(dimension="Accuracy", target_span="over budget")], "source_defects": []},
                   ("9000001-001", 2): {"findings": [finding(dimension="Fluency", subtype="grammar", target_span="over budget"),
                                                      finding(dimension="Accuracy", target_span="The renovation")], "source_defects": []}}
        main = self.section(self.run_all(outputs))
        # 第 1 次的 Accuracy「over budget」：第 2 次有 Fluency（維度不同）和 Accuracy「The renovation」（沒重疊），都配不到
        self.assertIn("- Main：總罰分 0（只計一致的 Finding）／15（連 uncertain 一起計）", main)   # 三個 major 各 5

    def test_findings_with_an_empty_target_span_are_paired_by_their_source_spans(self):
        outputs = {("9000001-001", 1): {"findings": [finding(target_span="", subtype="omission", source_span="延誤半年")], "source_defects": []},
                   ("9000001-001", 2): {"findings": [finding(target_span="", subtype="omission", source_span="半年，預算")], "source_defects": []}}
        main = self.section(self.run_all(outputs))
        self.assertIn("- Main：總罰分 5（只計一致的 Finding）／5（連 uncertain 一起計）", main)

    def test_when_only_one_call_succeeded_all_its_findings_are_uncertain(self):
        outputs = {("9000001-001", 1): {"findings": [finding()], "source_defects": []}, ("9000001-001", 2): gemini_samples.prompt_blocked()}
        main = self.section(self.run_all(outputs))
        self.assertIn("- Main：總罰分 0（只計一致的 Finding）／5（連 uncertain 一起計）", main)
        self.assertIn("- Unjudged 率：0/5（0.0%）", main)

    def test_when_both_calls_failed_the_component_is_unjudged_with_the_reasons_and_scores_nothing(self):
        outputs = {("9000001-001", 1): gemini_samples.prompt_blocked(), ("9000001-001", 2): gemini_samples.refusal()}
        main = self.section(self.run_all(outputs))
        self.assertIn("- Unjudged 率：1/5（20.0%）；原因（請求數）：block 1、refusal 1", main)
        self.assertIn("- 評完：5/5 個 Component", main)
        self.assertIn("每千字原文的罰分 0.0／0.0（原文 44 字）", main)   # 分母是原文總字數；Unjudged 的 Component 沒有 Judge 的 Finding

    def test_the_run_limit_does_not_make_the_runs_it_leaves_out_look_unfinished(self):
        self.runs = 3
        self.use_judging(mqm_run_limit={"a": 1})
        shutil.rmtree(self.data_dir)
        self.run_cli("translate", transport=FakeTransport(ok_response(), ok_response(), ok_response()))
        main = self.section(self.run_all())
        self.assertIn("- 評完：5/5 個 Component（尚未評完 0）", main)

    def test_a_component_that_has_not_been_judged_twice_yet_is_incomplete_not_unjudged(self):
        main = self.section(self.run_all(judge_args=("--limit", "7")))
        self.assertIn("- 評完：3/5 個 Component（尚未評完 2）", main)
        self.assertIn("- Unjudged 率：0/3（0.0%）", main)


class ScoringTest(ReportCase):
    def translate_without_the_paragraph(self):
        shutil.rmtree(self.data_dir)
        incomplete = good_translation(source_article())
        incomplete["components"] = [c for c in incomplete["components"] if c["id"] != "9000001-002"]
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(incomplete, ensure_ascii=False))))

    def test_an_omitted_component_costs_its_deterministic_penalty_so_dropping_text_never_scores_better(self):
        self.translate_without_the_paragraph()
        main = self.section(self.run_all())
        self.assertIn("- 評完：4/4 個 Component", main)
        # check 記的漏段 Finding（Accuracy／omission／major）要計入；分母是原文總字數 44，漏掉的段落也在內
        self.assertIn(f"- Main：總罰分 5（只計一致的 Finding）／5（連 uncertain 一起計）；每千字原文的罰分 {PENALTY_PER_1000}／{PENALTY_PER_1000}（原文 44 字）", main)
        self.assertIn("Accuracy/major 1（5）", main)

    def test_an_extra_component_and_findings_on_unjudged_components_are_scored_too(self):
        shutil.rmtree(self.data_dir)
        extra = good_translation(source_article())
        extra["components"].append({"id": "9000001-099", "type": "paragraph", "text": "Added by the model."})
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(extra, ensure_ascii=False))))
        outputs = {("9000001-001", 1): gemini_samples.prompt_blocked(), ("9000001-001", 2): gemini_samples.prompt_blocked()}
        main = self.section(self.run_all(outputs))
        self.assertIn("- Unjudged 率：1/5（20.0%）", main)
        self.assertIn("- Main：總罰分 5（只計一致的 Finding）", main)   # 多出的 Component：Locale & Format／major

    def test_fidelity_of_force_is_reported_but_never_scored(self):
        fof = finding(dimension="Fidelity-of-force", subtype="softening (b)", severity="critical", source_span="預算超支", target_span="over budget")
        main = self.section(self.run_all({"9000001-001": {"findings": [fof], "source_defects": []}}))
        self.assertIn("- Main：總罰分 0（只計一致的 Finding）／0（連 uncertain 一起計）", main)
        self.assertIn("- Fidelity-of-force（不計分，交人手審閱）：一致 1、uncertain 0", main)

    def test_main_and_peripheral_are_scored_apart(self):
        outputs = {"9000001-003": {"findings": [finding(source_span="施工中", target_span="under construction", severity="minor")], "source_defects": []},
                   "tags": {"findings": [finding(source_span="測試", target_span="Test", severity="major")], "source_defects": []}}
        main = self.section(self.run_all(outputs))
        self.assertIn("- Main：總罰分 0（", main)
        self.assertIn("- Peripheral：總罰分 6（只計一致的 Finding）／6（連 uncertain 一起計）；每千字原文的罰分 461.5／461.5（原文 13 字）", main)

    def test_findings_are_listed_by_dimension_and_severity(self):
        outputs = {"9000001-001": {"findings": [finding(), finding(dimension="Fluency", subtype="grammar", severity="minor", source_span="", target_span="six months late")],
                                   "source_defects": []}}
        main = self.section(self.run_all(outputs))
        self.assertIn("- Main 按 Dimension 與 Severity：只計一致：Accuracy/major 1（5）、Fluency/minor 1（1）；連 uncertain 一起計：Accuracy/major 1（5）、Fluency/minor 1（1）", main)

    def test_the_weights_come_from_the_experiment_config_and_changing_them_needs_no_new_judge_run(self):
        self.run_all({"9000001-001": {"findings": [finding()], "source_defects": []}})
        calls_before = self.calls()
        self.extra_experiment["weights"] = {"major": 10}
        self.write_json(self.config_path, self.experiment())
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        self.assertIn("- Main：總罰分 10（只計一致的 Finding）／10（連 uncertain 一起計）", self.section(self.report_text()))
        self.assertEqual(self.calls(), calls_before)

    def test_deterministic_findings_count_on_judged_components_and_do_not_need_the_judge(self):
        shutil.rmtree(self.data_dir)
        residue = good_translation(source_article())
        residue["components"][1]["text"] = "Resident 陳 said the delay left him deeply dissatisfied."   # 殘留漢字：Locale & Format minor
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(residue, ensure_ascii=False))))
        main = self.section(self.run_all())
        self.assertIn("- Main：總罰分 1（只計一致的 Finding）／1（連 uncertain 一起計）", main)

    def test_an_invalid_finding_is_counted_but_not_scored(self):
        outputs = {("9000001-001", 1): {"findings": [finding(), finding(source_span="不存在的片段")], "source_defects": []},
                   ("9000001-001", 2): {"findings": [finding()], "source_defects": []}}
        main = self.section(self.run_all(outputs))
        self.assertIn("- 無效 Finding 率：1/3（33.3%）", main)
        self.assertIn("- Main：總罰分 5（只計一致的 Finding）", main)


class SourceDefectTest(ReportCase):
    DEFECT = {"source_span": "延誤半年", "issue": "虛構的原文問題"}

    def defects(self, text):
        return text.split("## Source defect")[1].split("\n## ")[0]

    def test_the_same_defect_in_both_calls_is_listed_once_with_the_judge_that_found_it(self):
        text = self.run_all({"9000001-001": {"findings": [], "source_defects": [self.DEFECT]}})
        section = self.defects(text)
        self.assertEqual(section.count("延誤半年"), 1)
        self.assertIn("- 文章 9000001 ・ 9000001-001 ・「延誤半年」：虛構的原文問題（Judge j1）", section)

    def test_it_is_listed_once_across_approaches_and_the_report_names_every_judge_that_found_it(self):
        j2 = {**JUDGE, "id": "j2", "role": "experiment", "family": "qwen"}
        self.use_judging(judges=(JUDGE, j2))
        text = self.run_all({"9000001-001": {"findings": [], "source_defects": [self.DEFECT]}})
        section = self.defects(text)
        self.assertEqual(section.count("延誤半年"), 1)
        self.assertIn("（Judge j1、j2）", section)

    def test_a_defect_is_not_a_finding_and_costs_the_translator_nothing(self):
        main = self.section(self.run_all({"9000001-001": {"findings": [], "source_defects": [self.DEFECT]}}))
        self.assertIn("- Main：總罰分 0（只計一致的 Finding）／0（連 uncertain 一起計）", main)

    def test_text_a_judge_writes_cannot_start_a_new_line_or_heading_in_the_report(self):
        hostile = {"source_span": "延誤  半年", "issue": "x\n## Gate\n- 通過"}
        text = self.run_all({"9000001-001": {"findings": [], "source_defects": [hostile]}})
        self.assertNotIn("\n## Gate", text)
        self.assertIn("「延誤 半年」：x ## Gate - 通過（Judge j1）", self.defects(text))

    def test_the_section_says_so_when_no_judge_found_a_defect(self):
        self.assertIn("沒有 Judge 發現原文的問題", self.defects(self.run_all()))

    def test_a_suspected_correction_of_a_source_error_is_an_accuracy_finding_that_is_scored(self):
        corrected = finding(subtype="suspected correction of a source error", severity="minor")
        main = self.section(self.run_all({"9000001-001": {"findings": [corrected], "source_defects": []}}))
        self.assertIn("Accuracy/minor 1（1）", main)


class HeaderTest(ReportCase):
    def test_the_report_lists_the_judges_the_weights_the_normalisation_and_the_same_family_exceptions(self):
        self.use_judging(judges=({**JUDGE, "family": "gemini"},), same_family_exceptions=[{"approach": "a", "judge": "j1", "reason": "測試用的例外"}],
                         mqm_run_limit={"a": 1})
        text = self.run_all()
        self.assertLess(text.index("## Judge 設定"), text.index("## a × trad-to-en"))   # 開頭：在各 Approach 的結果之前
        header = text.split("## Judge 設定")[1].split("\n## ")[0]
        self.assertRegex(header, r"- j1（primary）：家族 gemini；provider gemini；模型 gemini-3.1-pro；prompt judge-mqm [0-9a-f]{64}；設定 hash [0-9a-f]{64}（10 個請求）")
        self.assertIn("- 同家族的例外：a × j1：測試用的例外", header)
        self.assertIn("- 權重：minor 1、major 5、critical 25", header)
        self.assertIn("- 正規化：每千字原文的罰分，分母是評完的 Component 的原文字數（不計空白）", header)
        self.assertIn("- MQM 只評：a 的第 1 個 Run 以內", header)

    def test_there_is_no_judge_section_when_no_judge_was_configured_or_run(self):
        del self.extra_experiment["judging"]
        self.write_json(self.config_path, self.experiment())
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        self.assertNotIn("Judge 設定", self.report_text())
        self.assertNotIn("Judge 評核", self.report_text())

    def test_a_judge_that_did_not_run_yet_is_listed_with_zero_requests_and_no_section(self):
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        text = self.report_text()
        self.assertIn("（0 個請求）", text)
        self.assertNotIn("× Judge j1", text)


class BaselineTest(ReportCase):
    def test_baseline_runs_are_judged_too_and_get_their_own_section(self):
        shutil.rmtree(self.data_dir)
        baseline_dir = os.path.join(self.tmp, "baseline")
        self.write_baseline(baseline_dir, "9000001", good_simplified_translation("9000001"))
        self.use_baseline(baseline_dir)
        self.write_json(self.config_path, {**self.experiment(), "directions": ["trad-to-simplified"]})
        self.assertEqual(self.run_cli("import-baseline")[0], 0)
        self.judge(judge_transport())
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        self.assertIn("### e × trad-to-simplified × Judge j1（primary）", self.report_text())


if __name__ == "__main__":
    unittest.main()
