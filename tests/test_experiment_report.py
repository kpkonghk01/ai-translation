"""Judge 實驗（Q31）：角色為 experiment 的 Judge（Qwen）對主 Judge（Gemini）的三個比較，Unjudged 與無效另列，而且不影響 Gate、分數和審閱清單。
從 main(argv) 進入：translate → judge → calibrate → check → report，transport 注入假的。只用虛構資料。

世界：兩篇文章（9000001 general、9000002 china-political），兩個 Approach（a 家族 gemini、b 家族 qwen），兩個 Judge（j1 primary、j2 experiment，家族 qwen）。
b 的譯文把「延誤半年」和 002 的歸因淡化了；a 的譯文是好的。主 Judge j1 判 b 的這幾條為 Weakened，a 的都是 Present。"""
import json
import os

import gemini_samples
from calibration_helpers import FACT_BUDGET, FACT_DELAY, FACT_WHO, J2, CalibrationCase, routed
from fake_transport import FakeTransport
from fixtures import APPROACH, JUDGE, good_translation, ok_response, source_article
from judge_helpers import fact_output, finding

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
    """主 Judge：b 的「延誤半年」與 002 的歸因是 Weakened，其餘 Present。"""
    if not is_b(payload):
        return fact_output(payload)
    return fact_output(payload, {**weaken(FACT_DELAY), **weaken(FACT_WHO, "a")} if payload["component"]["id"].endswith(("001", "002")) else {})


def experiment_facts(payload):
    """experiment 的 Judge：b 的「延誤半年」同意是 Weakened，002 的歸因判 Present（疑似漏看）；a 的「預算超支」多報 Weakened。"""
    if is_b(payload):
        return fact_output(payload, weaken(FACT_DELAY) if payload["component"]["id"].endswith("001") else {})
    return fact_output(payload, weaken(FACT_BUDGET, quote="over budget") if payload["component"]["id"] == "9000001-001" else {})


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

    def report(self) -> str:
        return self.report_text()

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
        two = self.comparison(self.experiment_section(self.report()), 2)
        self.assertIn("- general ・ 自己的譯文：疑似漏看 1/2（50.0%）；兩邊都判失敗 1；主 Judge 判失敗而這個 Judge 沒有最終 verdict：無；"
                      "這個 Judge 判失敗而主 Judge 判 Present：0；兩邊都判 Present：1", two)   # b 是 qwen 家族：j2 判自己的譯文；b 的「預算超支」兩邊都判 Present
        self.assertIn("- china-political ・ 自己的譯文：疑似漏看 1/1（100.0%）；兩邊都判失敗 0", two)
        self.assertIn("- general ・ 其他模型的譯文：疑似漏看 0/0（—）；兩邊都判失敗 0；主 Judge 判失敗而這個 Judge 沒有最終 verdict：無；這個 Judge 判失敗而主 Judge 判 Present：1；兩邊都判 Present：2", two)
        self.assertIn("- china-political ・ 其他模型的譯文：疑似漏看 0/0（—）", two)

    def test_the_locations_of_the_misses_are_listed_without_any_text(self):
        self.judge_all()
        two = self.comparison(self.experiment_section(self.report()), 2)
        self.assertIn("- general 的疑似漏看（1 條；原文、譯文和兩個 Judge 的判定在 experiment-judge-misses.md）：文章 9000001 ・ 9000001-002 ・ attribution", two)
        self.assertIn("- china-political 的疑似漏看（1 條", two)
        self.assertIn("文章 9000002 ・ 9000002-002 ・ attribution", two)

    def test_a_judge_that_gave_no_final_verdict_is_counted_apart_from_a_miss(self):
        blocked = gemini_samples.prompt_blocked()
        self.judge_all(experiment=lambda payload: blocked if payload["component"]["id"] == "9000002-002" else experiment_facts(payload))
        two = self.comparison(self.experiment_section(self.report()), 2)
        self.assertIn("- china-political ・ 自己的譯文：疑似漏看 0/0（—）；兩邊都判失敗 0；主 Judge 判失敗而這個 Judge 沒有最終 verdict：unjudged 1", two)   # 被擋不是「評了但漏看」

    def test_an_uncertain_verdict_is_not_a_miss_either(self):
        from itertools import count
        calls = count()
        flip = lambda payload: (fact_output(payload) if next(calls) % 2 else fact_output(payload, weaken(FACT_WHO, "a")))   # noqa: E731  兩次不一致
        self.judge_all(experiment=lambda payload: flip(payload) if payload["component"]["id"] == "9000002-002" and is_b(payload) else experiment_facts(payload))
        self.assertIn("沒有最終 verdict：uncertain 1", self.comparison(self.experiment_section(self.report()), 2))

    def test_only_the_primary_judges_failures_are_compared_so_a_fact_it_has_no_final_verdict_on_is_left_out(self):
        blocked = gemini_samples.prompt_blocked()
        flags_it = lambda payload: fact_output(payload, weaken(FACT_WHO, "a")) if payload["component"]["id"] == "9000002-002" and is_b(payload) else experiment_facts(payload)   # noqa: E731
        self.judge_all(primary=lambda payload: blocked if payload["component"]["id"] == "9000002-002" else primary_facts(payload), experiment=flags_it)
        two = self.comparison(self.experiment_section(self.report()), 2)
        political = next(line for line in two.splitlines() if line.startswith("- china-political ・ 自己的譯文"))
        self.assertIn("疑似漏看 0/0（—）；兩邊都判失敗 0；主 Judge 判失敗而這個 Judge 沒有最終 verdict：無；這個 Judge 判失敗而主 Judge 判 Present：0", political)   # 主 Judge 被擋：不是 Present，也不是失敗，不比

    def test_the_misses_file_has_the_texts_and_both_judges_verdicts_but_the_report_and_summary_have_none_of_them(self):
        self.judge_all()
        report = self.report()
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
        one = self.comparison(self.experiment_section(self.report()), 1)
        self.assertIn("- china-political 的變體：Judge j1（主）0/1（0.0%）；Judge j2 0/1（0.0%）；差（j2 − j1）+0.0 個百分點", one)   # j1 在 g04 是 uncertain：沒抓到
        self.assertIn("- general 的變體：Judge j1（主）2/2（100.0%）；Judge j2 0/2（0.0%）；差（j2 − j1）-100.0 個百分點", one)
        self.assertIn("- political − general：Judge j2 +0.0 個百分點；主 Judge -100.0 個百分點", one)
        self.assertIn("Judge 被擋（Unjudged）的變體算沒有抓到，分母不變", one)

    def test_without_a_calibration_it_says_so_instead_of_inventing_numbers(self):
        self.judge_all()
        self.assertIn("還沒有校準結果。", self.comparison(self.experiment_section(self.report()), 1))

    def test_a_judge_with_nothing_for_a_domain_cannot_be_compared_there(self):
        self.judge_all()
        self.calibrate_j1()   # 只有 j1 校準了
        one = self.comparison(self.experiment_section(self.report()), 1)
        self.assertIn("Judge j2 0/0（—）；差（j2 − j1）不能比（有一邊沒有資料）", one)


class HealthTest(ExperimentCase):
    def test_unjudged_and_invalid_are_listed_for_the_experiment_judge_by_domain_with_the_primary_judge_beside_it(self):
        blocked = gemini_samples.prompt_blocked()
        invalid = {"findings": [finding(source_span="不存在的片段")], "source_defects": []}
        self.judge_all(experiment_mqm={"9000002-001": blocked, ("9000001-001", 1): invalid})
        health = self.health(self.report())
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
        report = self.report()
        self.assertIn("事實 Unjudged 2/2（100.0%）", self.line(self.health(report), "- Judge j2（experiment）・ china-political"))
        self.assertIn("疑似漏看 0/0（—）", self.comparison(self.experiment_section(report), 2))   # 「根本不給評」不是「評了但漏看」
        self.assertIn("沒有最終 verdict：unjudged", self.comparison(self.experiment_section(report), 2))


class CrosscheckPointerTest(ExperimentCase):
    def test_without_a_crosscheck_setting_comparison_three_says_there_is_nothing_to_compare(self):
        self.judge_all()
        self.assertIn("沒有設定交叉檢驗（crosscheck）的對照模型。", self.comparison(self.experiment_section(self.report()), 3))

    def test_with_crosscheck_results_it_counts_the_sentences_the_reference_found_and_the_blind_model_did_not(self):
        self.judge_all()
        self.extra_experiment["crosscheck"] = {"judge": "j2", "reference_judge": "j1"}
        self.write_json(self.config_path, self.experiment())
        pick = lambda article_id: {"sentences": [{"component_id": f"{article_id}-002", "sentence": "市民陳虛構表示，工程拖延令他十分不滿。", "why": "虛構", "facts": [{"kind": "attribution", "fact": "不滿的是陳虛構本人"}]}]}   # noqa: E731
        article_of = lambda request: json.loads(json.loads(request.body)["contents"][0]["parts"][0]["text"])["components"][0]["id"].split("-")[0]
        from fake_transport import FunctionTransport

        def reply(request):
            model = request.url.split("/models/")[1].split(":")[0]
            return gemini_samples.ok(json.dumps(pick(article_of(request)) if model == PRIMARY_MODEL else {"sentences": []}, ensure_ascii=False))
        self.assertEqual(self.run_cli("crosscheck", transport=FunctionTransport(reply))[0], 0)
        three = self.comparison(self.experiment_section(self.report()), 3)
        self.assertIn("china-political 1 篇有對照結果：對照模型抽到而盲抽的模型沒抽到的句子 1 句；盲抽的模型本身被擋或失敗 0 篇", three)


class IndependenceTest(ExperimentCase):
    """角色為 experiment 的 Judge 不影響 Gate、分數和審閱清單：換成另一個結果天差地遠的 experiment Judge，這些都一模一樣。"""

    def gate_scores_and_review(self) -> tuple:
        report = self.report()
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
        report = self.report()
        self.assertNotIn("## Judge 實驗（Q31）", report)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "experiment-judge-misses.md")))

    def test_the_summary_has_the_numbers_but_no_text_and_no_free_text_block_reasons(self):
        blocked = gemini_samples.prompt_blocked("虛構的 原因 含空白")
        self.judge_all(experiment_mqm={"9000002-001": blocked})
        report, summary = self.report(), self.output_text("summary.md")
        self.assertIn("虛構的 原因 含空白", report)
        self.assertNotIn("虛構的 原因 含空白", summary)
        self.assertIn("block 的原因：（其他）", summary)
        self.assertIn("## Judge 實驗（Q31）", summary)
        self.assertNotIn(B_PARAGRAPH, summary)
