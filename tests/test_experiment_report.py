"""Judge 實驗（Q31）：角色為 experiment 的 Judge（Qwen）對主 Judge（Gemini）的三個比較，Unjudged 與無效另列，而且不影響 Gate、分數和審閱清單。
從 main(argv) 進入：translate → judge → calibrate → check → report，transport 注入假的。只用虛構資料。

世界：兩篇文章（9000001 general、9000002 china-political），兩個 Approach（a 家族 gemini、b 家族 qwen），兩個 Judge（j1 primary、j2 experiment，家族 qwen）。
b 的譯文把「延誤半年」和 002 的歸因淡化了；a 的譯文是好的。主 Judge j1 判 b 的這幾條為 Weakened，a 的都是 Present。"""
import json
import os

import gemini_samples
from calibration_helpers import FACT_BUDGET, FACT_DELAY, FACT_WHO, J2, CalibrationCase, routed
from fake_transport import FakeTransport, FunctionTransport
from fixtures import APPROACH, JUDGE, good_translation, ok_response, source_article
from judge_helpers import article_of, fact_output, finding, model_of

B_APPROACH = APPROACH | {"id": "b", "family": "qwen", "model": "gemini-3.1-other"}
B_HIGHLIGHT = "Renovation delayed by six months, over budget."
B_PARAGRAPH = "Resident Chan said the delay was a bit annoying."
PRIMARY_MODEL, EXPERIMENT_MODEL = "gemini-3.1-pro", "gemini-3.1-other"
EXCEPTION = {"approach": "b", "judge": "j2", "reason": "虛構的例外：測試 Qwen 判自己的譯文"}


def b_response(article_id):
    translation = good_translation(source_article(article_id))
    for component in translation["components"]:
        if component["type"] == "highlight":
            component["text"] = B_HIGHLIGHT
        elif component["type"] == "paragraph":
            component["text"] = B_PARAGRAPH
    return gemini_samples.ok(json.dumps(translation, ensure_ascii=False))


def is_b(payload) -> bool:
    return payload["component"]["translation"] in (B_HIGHLIGHT, B_PARAGRAPH)


def weaken(fact_text, type_="c", quote=None):
    """Weakened 的 verdict：引用要是譯文的逐字子字串（預設是 b 的譯文：001 是 B_HIGHLIGHT，002 是 B_PARAGRAPH）。"""
    return {fact_text: {"verdict": "Weakened", "type": type_, "quote": quote or ("delayed by six months" if type_ == "c" else "a bit annoying"), "reason": "虛構的理由"}}


def primary_facts(payload):
    """主 Judge：b 的「延誤半年」與 002 的歸因是 Weakened（fact_output 只套用在請求裡有的事實），其餘 Present。"""
    return fact_output(payload, {**weaken(FACT_DELAY), **weaken(FACT_WHO, "a")}) if is_b(payload) else fact_output(payload)


def experiment_facts(payload):
    """experiment 的 Judge：b 的「延誤半年」同意是 Weakened，002 的歸因判 Present（疑似漏看）；a 的「預算超支」多報 Weakened。"""
    if is_b(payload):
        return fact_output(payload, weaken(FACT_DELAY))
    return fact_output(payload, weaken(FACT_BUDGET, quote="over budget"))


class ExperimentCase(CalibrationCase):
    def setUp(self):
        super().setUp()
        b_path = os.path.join(self.tmp, "approach-b.json")
        self.write_json(b_path, B_APPROACH)
        self.extra_experiment["approaches"] = [{"config": self.approach_path, "runs": 1}, {"config": b_path, "runs": 1}]
        self.use_judging(judges=(JUDGE, J2), same_family_exceptions=[EXCEPTION])
        self.extra_experiment["calibration"] = {"gold_set": self.gold_path}
        self.write_json(self.config_path, self.experiment())
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), b_response("9000001"), ok_response("9000002"), b_response("9000002")))[0], 0)

    def judge_all(self, primary=primary_facts, experiment=experiment_facts, experiment_mqm=None, primary_mqm=None):
        """兩個 Judge 都跑 judge（MQM 與 fact）。fact 的輸出可以是函式（看請求的譯文），按 Component id 套用。"""
        by_component = lambda fn: {cid: fn for cid in ("9000001-001", "9000001-002", "9000002-002")}   # noqa: E731
        transport = routed({PRIMARY_MODEL: (primary_mqm or {}, by_component(primary)), EXPERIMENT_MODEL: (experiment_mqm or {}, by_component(experiment))})
        code, _, err = self.run_cli("judge", transport=transport)
        self.assertEqual(code, 0, err)

    def experiment_section(self, report: str) -> str:
        return report.split("## Judge 實驗（Q31）")[1].split("\n## ", 1)[0]

    def comparison(self, section: str, number: int) -> str:
        return section.split(f"#### 比較 {number}：")[1].split("\n#### ", 1)[0]

    def health(self, report: str) -> str:
        return self.experiment_section(report).split("#### Unjudged 率與無效 Finding 率")[1].split("\n#### ", 1)[0]

    @staticmethod
    def line(text: str, start: str) -> str:
        return next(line for line in text.splitlines() if line.startswith(start))


class ComparisonTwoTest(ExperimentCase):
    def test_suspected_misses_are_counted_by_domain_and_by_whose_translation_it_was(self):
        self.judge_all()
        two = self.comparison(self.experiment_section(self.report_text()), 2)
        self.assertIn("- general ・ 自己的譯文（主 Judge j1）：疑似漏看 1/2（50.0%）；兩邊都判失敗 1；主 Judge 判失敗而這個 Judge 沒有最終 verdict：無；"
                      "這個 Judge 判失敗而主 Judge 判 Present：0；兩邊都判 Present：1；主 Judge 判 Present 而這個 Judge 沒有最終 verdict：0；主 Judge 沒有最終 verdict（不比）：0", two)   # b 是 qwen 家族：j2 判自己的譯文；b 的「預算超支」兩邊都判 Present
        self.assertIn("- china-political ・ 自己的譯文（主 Judge j1）：疑似漏看 1/1（100.0%）；兩邊都判失敗 0", two)
        self.assertIn("- general ・ 其他 Approach 或 baseline 的譯文（主 Judge j1）：疑似漏看 0/0（—）；兩邊都判失敗 0；主 Judge 判失敗而這個 Judge 沒有最終 verdict：無；這個 Judge 判失敗而主 Judge 判 Present：1；兩邊都判 Present：2", two)
        self.assertIn("- china-political ・ 其他 Approach 或 baseline 的譯文（主 Judge j1）：疑似漏看 0/0（—）", two)

    def test_the_locations_of_the_misses_are_listed_without_any_text(self):
        self.judge_all()
        two = self.comparison(self.experiment_section(self.report_text()), 2)
        self.assertIn("- general 的疑似漏看（1 條；原文、譯文和兩個 Judge 的判定在 experiment-judge-misses.md）：文章 9000001 ・ 9000001-002 ・ attribution", two)
        self.assertIn("- china-political 的疑似漏看（1 條", two)
        self.assertIn("文章 9000002 ・ 9000002-002 ・ attribution", two)

    def test_a_judge_that_gave_no_final_verdict_is_counted_apart_from_a_miss(self):
        blocked = gemini_samples.prompt_blocked()
        self.judge_all(experiment=lambda payload: blocked if payload["component"]["id"] == "9000002-002" else experiment_facts(payload))
        two = self.comparison(self.experiment_section(self.report_text()), 2)
        self.assertIn("- china-political ・ 自己的譯文（主 Judge j1）：疑似漏看 0/0（—）；兩邊都判失敗 0；主 Judge 判失敗而這個 Judge 沒有最終 verdict：Unjudged 1", two)   # 被擋不是「評了但漏看」

    def test_an_uncertain_verdict_is_not_a_miss_either(self):
        # j2 對 9000002-002 的請求依序是 a 的兩次、b 的兩次（序號 1–4）：b 的第 2 次（序號 4）判 Weakened，第 1 次判 Present，兩次不一致
        second_says_weakened = {("9000002-002", 4): lambda p: fact_output(p, weaken(FACT_WHO, "a"))}
        transport = routed({PRIMARY_MODEL: ({}, {cid: primary_facts for cid in ("9000001-001", "9000001-002", "9000002-002")}),
                            EXPERIMENT_MODEL: ({}, {**{cid: experiment_facts for cid in ("9000001-001", "9000001-002")}, "9000002-002": experiment_facts, **second_says_weakened})})
        self.assertEqual(self.run_cli("judge", transport=transport)[0], 0)
        self.assertIn("沒有最終 verdict：uncertain", self.comparison(self.experiment_section(self.report_text()), 2))

    def test_only_the_primary_judges_failures_are_compared_so_a_fact_it_has_no_final_verdict_on_is_left_out(self):
        blocked = gemini_samples.prompt_blocked()
        flags_it = lambda payload: fact_output(payload, weaken(FACT_WHO, "a")) if payload["component"]["id"] == "9000002-002" and is_b(payload) else experiment_facts(payload)   # noqa: E731
        self.judge_all(primary=lambda payload: blocked if payload["component"]["id"] == "9000002-002" else primary_facts(payload), experiment=flags_it)
        two = self.comparison(self.experiment_section(self.report_text()), 2)
        political = next(line for line in two.splitlines() if line.startswith("- china-political ・ 自己的譯文"))
        self.assertIn("疑似漏看 0/0（—）；兩邊都判失敗 0；主 Judge 判失敗而這個 Judge 沒有最終 verdict：無；這個 Judge 判失敗而主 Judge 判 Present：0", political)   # 主 Judge 被擋：不是 Present，也不是失敗，不比
        self.assertIn("主 Judge 沒有最終 verdict（不比）：1", political)   # 不比，但要數出來

    def test_the_misses_file_has_the_texts_and_both_judges_verdicts_but_the_report_and_summary_have_none_of_them(self):
        self.judge_all()
        report = self.report_text()
        misses = self.output_text("experiment-judge-misses.md")
        for text in (B_PARAGRAPH, "市民陳虛構表示，工程拖延令他十分不滿。", f"事實（attribution）：{FACT_WHO}", "Judge j1（主）：第 1 次 Weakened", "Judge j2（experiment）：第 1 次 Present"):
            self.assertIn(text, misses)
            self.assertNotIn(text, report)
            self.assertNotIn(text, self.output_text("summary.md"))
        self.assertIn("引用「a bit annoying」", misses)


class ComparisonOneTest(ExperimentCase):
    def calibrate_both(self):
        self.calibrate_j1()
        self.assertEqual(self.calibrate("--judge", "j2", transport=routed({EXPERIMENT_MODEL: ({}, {})}))[0], 0)   # j2 把每條事實都判 Present

    def test_the_recall_on_political_and_general_variants_and_the_gaps_come_from_the_calibration(self):
        self.judge_all()
        self.calibrate_both()
        one = self.comparison(self.experiment_section(self.report_text()), 1)
        self.assertIn("- china-political 的變體：Judge j1（主）0/1（0.0%）（uncertain 的事實（算沒有抓到） 1）；Judge j2 0/1（0.0%）；差（j2 − j1）+0.0 個百分點", one)   # j1 在 g04 是 uncertain：沒抓到，而且寫出來
        self.assertIn("- general 的變體：Judge j1（主）2/2（100.0%）；Judge j2 0/2（0.0%）；差（j2 − j1）-100.0 個百分點", one)
        self.assertIn("- political − general：Judge j2 +0.0 個百分點；主 Judge -100.0 個百分點", one)
        self.assertIn("Judge 被擋（Unjudged）的變體算沒有抓到，分母不變", one)

    def test_without_a_calibration_it_says_so_instead_of_inventing_numbers(self):
        self.judge_all()
        self.assertIn("還沒有校準結果。", self.comparison(self.experiment_section(self.report_text()), 1))

    def test_a_judge_with_nothing_for_a_domain_cannot_be_compared_there(self):
        self.judge_all()
        self.calibrate_j1()   # 只有 j1 校準了
        one = self.comparison(self.experiment_section(self.report_text()), 1)
        self.assertIn("Judge j2 0/0（—）（尚未評完的項目 1）；差（j2 − j1）不能比（有一邊沒有資料）", one)   # 沒有資料的原因也寫出來


class HealthTest(ExperimentCase):
    def test_unjudged_and_invalid_are_listed_for_the_experiment_judge_by_domain_with_the_primary_judge_beside_it(self):
        blocked = gemini_samples.prompt_blocked()
        invalid = {"findings": [finding(source_span="不存在的片段")], "source_defects": []}
        self.judge_all(experiment_mqm={"9000002-001": blocked, ("9000001-001", 1): invalid})
        health = self.health(self.report_text())
        political = self.line(health, "- Judge j2（experiment）・ china-political")
        self.assertIn("MQM Unjudged 2/10（20.0%）", political)   # 兩個 Run 的 001 都被擋
        self.assertIn("block 的原因：prompt:PROHIBITED_CONTENT 4", political)
        self.assertIn("無效 Finding 率 0/0（—）", political)
        general = self.line(health, "- Judge j2（experiment）・ general")
        self.assertIn("MQM Unjudged 0/10（0.0%）", general)
        self.assertIn("無效 Finding 率 1/1（100.0%）", general)   # 只有一次的輸出有一個不是逐字的片段
        self.assertIn("MQM Unjudged 0/10（0.0%）", self.line(health, "- Judge j1（primary）・ china-political"))   # 主 Judge 作對照

    def test_a_blocked_fact_request_is_unjudged_for_facts_and_stays_out_of_the_misses(self):
        blocked = gemini_samples.prompt_blocked()
        self.judge_all(experiment=lambda payload: blocked)
        report = self.report_text()
        self.assertIn("事實 Unjudged 2/2（100.0%）", self.line(self.health(report), "- Judge j2（experiment）・ china-political"))
        self.assertIn("疑似漏看 0/0（—）", self.comparison(self.experiment_section(report), 2))   # 「根本不給評」不是「評了但漏看」
        self.assertIn("沒有最終 verdict：Unjudged", self.comparison(self.experiment_section(report), 2))
        other = next(line for line in self.comparison(self.experiment_section(report), 2).splitlines() if line.startswith("- general ・ 其他 Approach 或 baseline 的譯文"))
        self.assertIn("主 Judge 判 Present 而這個 Judge 沒有最終 verdict：3", other)   # a 的三條事實主 Judge 都判 Present，j2 被擋：不算「兩邊都判 Present」


class CrosscheckPointerTest(ExperimentCase):
    def test_without_a_crosscheck_setting_comparison_three_says_there_is_nothing_to_compare(self):
        self.judge_all()
        self.assertIn("沒有設定交叉檢驗（crosscheck）的對照模型。", self.comparison(self.experiment_section(self.report_text()), 3))

    def test_with_crosscheck_results_it_counts_the_sentences_the_reference_found_and_the_blind_model_did_not(self):
        self.judge_all()
        self.extra_experiment["crosscheck"] = {"judge": "j2", "reference_judge": "j1"}
        self.write_json(self.config_path, self.experiment())
        pick = lambda article_id: {"sentences": [{"component_id": f"{article_id}-002", "sentence": "市民陳虛構表示，工程拖延令他十分不滿。", "why": "虛構", "facts": [{"kind": "attribution", "fact": "不滿的是陳虛構本人"}]}]}   # noqa: E731

        def reply(request):
            return gemini_samples.ok(json.dumps(pick(article_of(request)) if model_of(request) == PRIMARY_MODEL else {"sentences": []}, ensure_ascii=False))
        self.assertEqual(self.run_cli("crosscheck", transport=FunctionTransport(reply))[0], 0)
        three = self.comparison(self.experiment_section(self.report_text()), 3)
        self.assertIn("china-political：兩個模型都有結果的 1 篇，對照模型抽到而盲抽的模型沒抽到的句子 1 句；盲抽的模型被擋或失敗 0 篇、還沒有結果 0 篇", three)


class IndependenceTest(ExperimentCase):
    """角色為 experiment 的 Judge 不影響 Gate、分數和審閱清單：換成另一個結果天差地遠的 experiment Judge，這些都一模一樣。"""

    def gate_scores_and_review(self) -> tuple:
        report = self.report_text()
        gate = report.split("## Gate（Must-retain fact）")[1].split("\n## ", 1)[0]
        primary = report.split("### a × trad-to-en × Judge j1（primary）")[1].split("\n#", 1)[0]
        primary_b = report.split("### b × trad-to-en × Judge j1（primary）")[1].split("\n#", 1)[0]
        self.assertEqual(self.run_cli("review")[0], 0)
        return gate, primary, primary_b, self.output_text("review.md")

    def test_the_gate_the_primary_judges_scores_and_the_review_list_do_not_change_with_the_experiment_judge(self):
        everything_weakened = lambda payload: fact_output(payload, {f: {"verdict": "Absent", "type": "d", "quote": ""} for f in (FACT_DELAY, FACT_BUDGET, FACT_WHO)})   # noqa: E731
        self.judge_all(experiment=everything_weakened, experiment_mqm={"9000001-001": {"findings": [finding(severity="critical")], "source_defects": []}})
        loud = self.gate_scores_and_review()
        os.remove(os.path.join(self.out_dir, "judge-calls.jsonl"))
        self.judge_all(experiment=lambda payload: fact_output(payload))   # experiment 的 Judge 什麼都沒抓到
        quiet = self.gate_scores_and_review()
        self.assertEqual(loud, quiet)
        self.assertIn("淡化率：", loud[0])

    def test_a_world_without_any_experiment_judge_has_no_experiment_section_and_no_misses_file(self):
        self.use_judging(judges=(JUDGE,), same_family_exceptions=[])
        self.extra_experiment["calibration"] = {"gold_set": self.gold_path}
        self.write_json(self.config_path, self.experiment())
        transport = routed({PRIMARY_MODEL: ({}, {cid: primary_facts for cid in ("9000001-001", "9000001-002", "9000002-002")})})
        self.assertEqual(self.run_cli("judge", transport=transport)[0], 0)
        report = self.report_text()
        self.assertNotIn("## Judge 實驗（Q31）", report)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "experiment-judge-misses.md")))

    def test_the_summary_has_the_numbers_but_no_text_and_no_free_text_block_reasons(self):
        blocked = gemini_samples.prompt_blocked("虛構的 原因 含空白")
        self.judge_all(experiment_mqm={"9000002-001": blocked})
        report, summary = self.report_text(), self.output_text("summary.md")
        self.assertIn("虛構的 原因 含空白", report)
        self.assertNotIn("虛構的 原因 含空白", summary)
        self.assertIn("block 的原因：（其他）", summary)
        self.assertIn("## Judge 實驗（Q31）", summary)
        self.assertNotIn(B_PARAGRAPH, summary)


THIRD_MODEL = "gemini-3.1-third"
J3 = JUDGE | {"id": "j3", "role": "primary", "family": "claude", "model": THIRD_MODEL}


class ReferenceByGroupTest(ExperimentCase):
    """參考的主 Judge 按 Gate 的分組取（可以被 group_overrides 與 primary_judges 改掉），不是按文章的 domain。"""

    def test_a_group_with_its_own_primary_judge_is_compared_against_that_judge_and_the_rows_say_which(self):
        self.use_judging(judges=(JUDGE, J2, J3), same_family_exceptions=[EXCEPTION], primary_judges={"q34": "j3"})
        self.extra_experiment["ground_truth"]["group_overrides"] = {"9000002": "q34"}
        self.extra_experiment["calibration"] = {"gold_set": self.gold_path}
        self.write_json(self.config_path, self.experiment())
        every = ("9000001-001", "9000001-002", "9000002-002")
        # j3（q34 的主 Judge）把所有事實判 Present；j1 仍是 general 的主 Judge，判 b 的兩條為 Weakened
        transport = routed({PRIMARY_MODEL: ({}, {cid: primary_facts for cid in every}), EXPERIMENT_MODEL: ({}, {cid: experiment_facts for cid in every}),
                            THIRD_MODEL: ({}, {cid: fact_output for cid in every})})
        self.assertEqual(self.run_cli("judge", transport=transport)[0], 0)
        two = self.comparison(self.experiment_section(self.report_text()), 2)
        self.assertIn("- q34 ・ 自己的譯文（主 Judge j3）：疑似漏看 0/0（—）", two)   # j3 沒有判任何失敗：沒有東西可以漏看
        self.assertIn("- general ・ 自己的譯文（主 Judge j1）：疑似漏看 1/2（50.0%）", two)
        self.assertNotIn("china-political ・", two)   # 這篇文章屬於 q34 這個分組，不是 china-political
        self.assertNotIn("疑似漏看 1/1", two)   # 如果誤用 j1 當 q34 的參考，會多出一個 j3 根本沒判出來的「漏看」


class StaleFileTest(ExperimentCase):
    def test_the_misses_file_with_the_original_texts_is_removed_when_there_is_nothing_to_list_any_more(self):
        self.judge_all()
        self.report_text()
        self.assertIn(B_PARAGRAPH, self.output_text("experiment-judge-misses.md"))
        self.use_judging(judges=(JUDGE,), same_family_exceptions=[])   # 拿掉 experiment 的 Judge
        self.extra_experiment["calibration"] = {"gold_set": self.gold_path}
        self.write_json(self.config_path, self.experiment())
        report = self.report_text()
        self.assertNotIn("## Judge 實驗（Q31）", report)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "experiment-judge-misses.md")))   # 舊的含原文，不能留著

    def test_the_file_is_removed_when_a_rerun_finds_no_misses(self):
        self.judge_all()
        self.report_text()
        self.assertTrue(os.path.exists(os.path.join(self.out_dir, "experiment-judge-misses.md")))
        os.remove(os.path.join(self.out_dir, "judge-calls.jsonl"))
        self.judge_all(experiment=primary_facts)   # 這次 experiment 的 Judge 與主 Judge 的判定一樣：沒有疑似漏看
        self.report_text()
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "experiment-judge-misses.md")))


class ScreeningHealthTest(ExperimentCase):
    def test_the_full_text_screening_is_part_of_the_unjudged_rate_apart_from_misses(self):
        self.use_judging(judges=(JUDGE, J2), same_family_exceptions=[EXCEPTION], screening=True)
        self.extra_experiment["calibration"] = {"gold_set": self.gold_path}
        self.write_json(self.config_path, self.experiment())
        every = ("9000001-001", "9000001-002", "9000002-002")
        blocked = gemini_samples.prompt_blocked()
        transport = routed({PRIMARY_MODEL: ({}, {cid: primary_facts for cid in every}),
                            EXPERIMENT_MODEL: ({}, {cid: experiment_facts for cid in every}, {n: blocked for n in range(1, 9)})})   # j2 的全文篩查全被擋（4 個 Run × 2 次）
        self.assertEqual(self.run_cli("judge", transport=transport)[0], 0)
        health = self.health(self.report_text())
        self.assertIn("全文篩查 Unjudged 2/2（100.0%）（Run）", self.line(health, "- Judge j2（experiment）・ china-political"))
        self.assertIn("全文篩查 Unjudged 0/2（0.0%）（Run）", self.line(health, "- Judge j1（primary）・ general"))   # 主 Judge 作對照


class ValidityAndSourceTest(ExperimentCase):
    def test_an_invalid_verdict_is_counted_with_its_denominator(self):
        bad_quote = lambda payload: fact_output(payload, {FACT_DELAY: {"verdict": "Weakened", "type": "c", "quote": "not in the translation"}})   # noqa: E731
        self.judge_all(experiment=lambda payload: bad_quote(payload) if payload["component"]["id"] == "9000001-001" and is_b(payload) else experiment_facts(payload))
        general = self.line(self.health(self.report_text()), "- Judge j2（experiment）・ general")
        self.assertIn("無效的 verdict 2/", general)   # b 的 001 兩次請求各有一個作廢的 verdict
        self.assertNotIn("無效的 verdict 0/0", general)

    def test_the_family_is_compared_ignoring_case_and_spaces(self):
        self.write_json(os.path.join(self.tmp, "approach-b.json"), B_APPROACH | {"family": " QWEN "})
        self.judge_all()
        two = self.comparison(self.experiment_section(self.report_text()), 2)
        self.assertIn("- general ・ 自己的譯文（主 Judge j1）：疑似漏看 1/2", two)

    def test_an_approach_that_is_no_longer_in_the_experiment_is_a_source_of_its_own_not_other(self):
        self.judge_all()
        self.extra_experiment["approaches"] = self.extra_experiment["approaches"][:1]   # 拿掉 b，紀錄還在
        self.use_judging(judges=(JUDGE, J2), same_family_exceptions=[])   # 指向 b 的同家族例外也要拿掉，不然設定不合法
        self.extra_experiment["calibration"] = {"gold_set": self.gold_path}
        self.write_json(self.config_path, self.experiment())
        two = self.comparison(self.experiment_section(self.report_text()), 2)
        self.assertIn("- general ・ Approach 不在實驗設定內的譯文（主 Judge j1）：疑似漏看 1/2", two)
        self.assertNotIn("自己的譯文", two)


class CrosscheckFailureTest(ExperimentCase):
    def run_crosscheck(self, blind):
        self.judge_all()
        self.extra_experiment["crosscheck"] = {"judge": "j2", "reference_judge": "j1"}
        self.write_json(self.config_path, self.experiment())
        pick = {"sentences": [{"component_id": "9000002-002", "sentence": "市民陳虛構表示，工程拖延令他十分不滿。", "why": "虛構", "facts": [{"kind": "attribution", "fact": "不滿的是陳虛構本人"}]}]}

        def reply(request):
            return gemini_samples.ok(json.dumps(pick, ensure_ascii=False)) if model_of(request) == PRIMARY_MODEL else blind
        self.assertEqual(self.run_cli("crosscheck", transport=FunctionTransport(reply))[0], 0)
        return self.comparison(self.experiment_section(self.report_text()), 3)

    def test_a_blocked_blind_model_is_counted_as_blocked_and_its_article_adds_nothing_to_the_sentence_count(self):
        three = self.run_crosscheck(gemini_samples.prompt_blocked())
        self.assertIn("兩個模型都有結果的 0 篇，對照模型抽到而盲抽的模型沒抽到的句子 0 句；盲抽的模型被擋或失敗 1 篇、還沒有結果 0 篇", three)

    def test_a_blind_model_that_ran_and_found_nothing_leaves_the_reference_sentences_as_gaps(self):
        three = self.run_crosscheck(gemini_samples.ok(json.dumps({"sentences": []})))
        self.assertIn("兩個模型都有結果的 1 篇，對照模型抽到而盲抽的模型沒抽到的句子 1 句；盲抽的模型被擋或失敗 0 篇", three)

    def test_a_missing_reference_result_is_counted_instead_of_being_skipped(self):
        self.judge_all()
        self.extra_experiment["crosscheck"] = {"judge": "j2", "reference_judge": "j1"}
        self.write_json(self.config_path, self.experiment())
        self.assertEqual(self.run_cli("crosscheck", "--article", "9000001", transport=routed({PRIMARY_MODEL: ({}, {}), EXPERIMENT_MODEL: ({}, {})}))[0], 0)   # 只跑 general：政治文章沒有結果
        three = self.comparison(self.experiment_section(self.report_text()), 3)
        self.assertIn("對照模型沒有結果 1 篇", three)
