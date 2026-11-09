"""Gate：淡化率、95% 信心上限、分母、分組、主 Judge、「未完成」。從 main(argv) 進入：translate → check → judge → report。只用虛構資料。"""
import copy
import json
import os
import shutil

import gemini_samples
from fake_transport import FakeTransport
from fixtures import GROUND_TRUTH, JUDGE, Workspace, ok_response, source_article
from judge_helpers import fact_output, fact_transport, user_payload
from test_gate_facts import FactCase

S1_FACT = "工程延誤半年"   # 句 1（9000001-001）的兩條事實之一
S1_OTHER = "預算超支"
S2_FACT = "不滿的是市民陳虛構本人"   # 句 2（9000001-002）


def weakened(fact_text=S1_FACT, kind_type="e"):
    """兩次都判這條事實 Weakened。"""
    change = {"verdict": "Weakened", "type": kind_type, "quote": "six months late"}
    return {"9000001-001": lambda p: fact_output(p, {fact_text: change})}


class GateCase(FactCase):
    def gate(self, transport=None, *, judge_args=("--task", "fact")):
        """judge 之後 check、report，回傳報告全文。"""
        code, _, err = self.run_cli("judge", *judge_args, transport=transport or fact_transport())
        self.assertEqual(code, 0, err)
        return self.report()

    def report(self) -> str:
        code, _, err = self.run_cli("check")
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def section(report: str, heading_start: str) -> str:
        """某一個 ### 小節的內容（到下一個 ## 或 ### 為止）。"""
        lines, out, inside = report.splitlines(), [], False
        for line in lines:
            if line.startswith(("## ", "### ")):
                inside = line.startswith(heading_start)
                continue
            if inside:
                out.append(line)
        return "\n".join(out)


class RateTest(GateCase):
    def test_when_every_fact_is_present_nothing_fails_and_the_upper_bound_follows_the_sample_size(self):
        gate = self.section(self.gate(), "### a × trad-to-en ・ 分組 general")
        self.assertIn("淡化率：0/2（0.0%）", gate)
        self.assertIn("95% 上限 77.6%", gate)   # 0/2：1 − 0.05^(1/2)，不靠程式裡的算法
        self.assertIn("狀態：完成", gate)
        self.assertNotIn("未完成", gate)

    def test_one_weakened_fact_fails_its_whole_sentence_however_many_facts_the_sentence_has(self):
        gate = self.section(self.gate(fact_transport(weakened())), "### a × trad-to-en ・ 分組 general")
        self.assertIn("淡化率：1/2（50.0%）", gate)   # 句 1 失敗；它另一條事實是 Present，不影響
        self.assertIn("95% 上限 97.5%", gate)   # 1/2：√0.95

    def test_a_weakened_fact_counts_by_type_and_kind_and_the_failed_facts_not_the_sentences(self):
        gate = self.section(self.gate(fact_transport(weakened(kind_type="e"))), "### a × trad-to-en ・ 分組 general")
        self.assertIn("e 1", gate)
        for other in ("a 0", "b 0", "c 0", "d 0", "f 0", "未標類型 0"):
            self.assertIn(other, gate)
        self.assertIn("detail 1/1", gate)   # 失敗的事實／有結果的事實
        self.assertIn("act 0/1", gate)
        self.assertIn("attribution 0/1", gate)

    def test_a_weakened_fact_without_a_type_or_with_two_different_types_is_counted_as_untyped(self):
        outputs = {("9000001-001", 1): lambda p: fact_output(p, {S1_FACT: {"verdict": "Weakened", "type": "e", "quote": "late"}}),
                   ("9000001-001", 2): lambda p: fact_output(p, {S1_FACT: {"verdict": "Weakened", "type": "b", "quote": "late"}})}
        gate = self.section(self.gate(fact_transport(outputs)), "### a × trad-to-en ・ 分組 general")
        self.assertIn("淡化率：1/2（50.0%）", gate)   # 判定一致（都是 Weakened），類型不一致不影響 Gate
        self.assertIn("未標類型 1", gate)

    def test_an_absent_fact_fails_the_sentence_and_is_listed_as_absent(self):
        change = {"verdict": "Absent", "type": "d", "quote": ""}
        gate = self.section(self.gate(fact_transport({"9000001-001": lambda p: fact_output(p, {S1_OTHER: change})})), "### a × trad-to-en ・ 分組 general")
        self.assertIn("淡化率：1/2（50.0%）", gate)
        self.assertIn("d 1", gate)
        self.assertIn("其中 Absent 1", gate)

    def test_amplification_is_counted_apart_and_does_not_fail_the_sentence(self):
        change = {"verdict": "Present", "type": "amplification"}
        gate = self.section(self.gate(fact_transport({"9000001-001": lambda p: fact_output(p, {S1_FACT: change})})), "### a × trad-to-en ・ 分組 general")
        self.assertIn("淡化率：0/2（0.0%）", gate)
        self.assertIn("Amplification（另計，不計入淡化率）：1", gate)

    def test_a_component_missing_from_the_translation_makes_all_its_facts_absent_without_asking_the_judge(self):
        shutil.rmtree(self.data_dir)
        gone = json.loads(json.loads(ok_response().body)["candidates"][0]["content"]["parts"][0]["text"])
        gone["components"] = [c for c in gone["components"] if c["id"] != "9000001-002"]
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(gone, ensure_ascii=False))))
        transport = fact_transport()
        gate = self.section(self.gate(transport), "### a × trad-to-en ・ 分組 general")
        self.assertEqual({user_payload(r)["component"]["id"] for r in transport.requests}, {"9000001-001"})
        self.assertIn("淡化率：1/2（50.0%）", gate)   # 漏段造成的 Absent 計入分母，算作失敗
        self.assertIn("其中 Absent 1", gate)
        self.assertIn("attribution 1/1", gate)

    def test_zero_failures_in_150_sentences_gives_an_upper_bound_of_about_two_percent(self):
        n = 150
        article = {"title": "虛構標題", "mainChannel": "社會新聞", "tags": [],
                   "components": [{"id": f"9000001-{i:03d}", "type": "paragraph", "text": f"虛構的第 {i} 段句子。"} for i in range(1, n + 1)],
                   "meta": {"source": "public-web", "articleId": 9000001, "unknown": [], "warnings": []}}
        translation = {"title": "Fictional title", "tags": [],
                       "components": [{"id": f"9000001-{i:03d}", "type": "paragraph", "text": f"Fictional sentence number {i}."} for i in range(1, n + 1)]}
        self.write_json(os.path.join(self.articles_dir, "9000001.json"), article)
        gt = {"articles": [{"article_id": "9000001", "role": "sensitive", "domain": "general", "note": "虛構",
                            "items": [{"component_id": f"9000001-{i:03d}", "anchor": f"第 {i} 段", "why": "虛構",
                                       "must_retain": [{"kind": "act", "fact": f"虛構的第 {i} 段發生了某事"}]} for i in range(1, n + 1)]}]}
        self.use_ground_truth(gt)
        shutil.rmtree(self.data_dir)
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(translation, ensure_ascii=False))))
        gate = self.section(self.gate(), "### a × trad-to-en ・ 分組 general")
        self.assertIn("淡化率：0/150（0.0%）", gate)
        self.assertIn("95% 上限 2.0%", gate)


class DenominatorTest(GateCase):
    def test_a_sentence_counts_once_however_many_runs_it_failed_in_and_each_run_has_its_own_rate(self):
        shutil.rmtree(self.data_dir)
        self.runs = 2
        self.write_json(self.config_path, self.experiment())
        self.run_cli("translate", transport=FakeTransport(ok_response(), ok_response()))
        seen = {"n": 0}

        def first_run_only(payload):   # 句 1 的請求：第 1 個 Run 的兩次呼叫判 Weakened，第 2 個 Run 的不判
            seen["n"] += 1
            return fact_output(payload, {S1_FACT: {"verdict": "Weakened", "type": "e", "quote": "late"}}) if seen["n"] <= 2 else fact_output(payload)

        gate = self.section(self.gate(fact_transport({"9000001-001": first_run_only})), "### a × trad-to-en ・ 分組 general")
        self.assertIn("淡化率：1/2（50.0%）", gate)   # 不是 1/4
        self.assertIn("Run 1 1/2（50.0%）", gate)
        self.assertIn("Run 2 0/2（0.0%）", gate)

    def test_runs_that_were_blocked_or_refused_are_left_out_of_the_denominator_and_listed(self):
        shutil.rmtree(self.data_dir)
        self.article_ids = ("9000001", "9000002", "9000003")
        self.write_json(os.path.join(self.articles_dir, "9000002.json"), source_article("9000002"))
        self.write_json(os.path.join(self.articles_dir, "9000003.json"), source_article("9000003"))
        self.use_ground_truth(self.gt_for(("9000001", "general"), ("9000002", "general"), ("9000003", "general")))
        self.rewrite_experiment(lambda e: e["articles"].update(ids=list(self.article_ids)))
        self.run_cli("translate", transport=FakeTransport(ok_response(), gemini_samples.prompt_blocked(), gemini_samples.refusal()))
        gate = self.section(self.gate(), "### a × trad-to-en ・ 分組 general")
        self.assertIn("淡化率：0/2（0.0%）", gate)   # 只有 9000001 的兩句
        self.assertIn("未計入的 Run：block 1、refusal 1、其他失敗 0", gate)

    @staticmethod
    def gt_for(*articles):
        out = []
        for article_id, domain in articles:
            gt = copy.deepcopy(GROUND_TRUTH["articles"][0])
            gt["article_id"], gt["domain"] = article_id, domain
            for item in gt["items"]:
                item["component_id"] = item["component_id"].replace("9000001", article_id)
            out.append(gt)
        return {"articles": out}


class CompletenessTest(GateCase):
    def gate_section(self, transport):
        return self.section(self.gate(transport), "### a × trad-to-en ・ 分組 general")

    def test_two_calls_that_disagree_leave_the_fact_uncertain_and_the_gate_unfinished(self):
        outputs = {("9000001-001", 1): lambda p: fact_output(p),
                   ("9000001-001", 2): lambda p: fact_output(p, {S1_FACT: {"verdict": "Weakened", "type": "e", "quote": "late"}})}
        gate = self.gate_section(fact_transport(outputs))
        self.assertIn("狀態：未完成", gate)
        self.assertIn("uncertain 1", gate)
        self.assertIn("淡化率：0/1（0.0%）", gate)   # 句 1 還沒有結果，不在分母，也沒有失敗；句 2 已通過

    def test_a_failure_that_is_certain_is_counted_even_while_other_facts_are_still_pending(self):
        outputs = {("9000001-001", 1): lambda p: fact_output(p, {S1_FACT: {"verdict": "Weakened", "type": "e", "quote": "late"}}),
                   ("9000001-001", 2): lambda p: fact_output(p, {S1_FACT: {"verdict": "Weakened", "type": "e", "quote": "late"},
                                                                 S1_OTHER: {"verdict": "Absent", "quote": ""}}),
                   ("9000001-002", 1): lambda p: fact_output(p)}
        gate = self.gate_section(fact_transport(outputs))
        self.assertIn("淡化率：1/2（50.0%）", gate)
        self.assertIn("狀態：未完成", gate)   # 預算超支 兩次不一致，還是待審

    def test_one_successful_call_makes_its_verdicts_uncertain_and_two_failed_calls_make_them_unjudged(self):
        gate = self.gate_section(fact_transport({("9000001-001", 1): gemini_samples.prompt_blocked()}))
        self.assertIn("uncertain 2", gate)   # 句 1 的兩條事實
        self.forget_calls()
        gate = self.gate_section(fact_transport({"9000001-001": gemini_samples.prompt_blocked()}))
        self.assertIn("Unjudged 2", gate)
        self.assertIn("狀態：未完成", gate)
        self.assertIn("淡化率：0/1（0.0%）", gate)

    def test_a_verdict_voided_by_its_quote_is_listed_as_invalid_and_the_gate_stays_unfinished(self):
        void = {"verdict": "Present", "quote": "the budget was exceeded"}
        gate = self.gate_section(fact_transport({"9000001-001": lambda p: fact_output(p, {S1_OTHER: void})}))
        self.assertIn("無效 1", gate)
        self.assertIn("狀態：未完成", gate)

    def test_facts_that_were_not_judged_yet_are_incomplete_not_unjudged(self):
        self.run_cli("judge", "--task", "fact", "--limit", "1", transport=fact_transport())
        gate = self.section(self.report(), "### a × trad-to-en ・ 分組 general")
        self.assertIn("尚未評完 3", gate)   # 一個請求只是句 1 的第一次，其餘都還沒評
        self.assertIn("狀態：未完成", gate)
        self.assertIn("Unjudged 0", gate)

    def test_changing_the_ground_truth_after_judging_makes_the_old_verdicts_stale(self):
        self.gate()
        self.edited_ground_truth(lambda a: a["items"][1]["must_retain"][0].update(fact="不滿的是市民本人"))
        gate = self.section(self.report(), "### a × trad-to-en ・ 分組 general")
        self.assertIn("過期 1", gate)
        self.assertIn("狀態：未完成", gate)

    def test_the_gate_says_the_judge_recall_has_not_been_measured(self):
        self.assertIn("Judge recall 未量度", self.section(self.gate(), "### a × trad-to-en ・ 分組 general"))


class GroupingTest(GateCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.data_dir)
        self.article_ids = ("9000001", "9000002")
        self.write_json(os.path.join(self.articles_dir, "9000002.json"), source_article("9000002"))
        self.groups = (("9000001", "general"), ("9000002", "china-political"))
        self.use_groups()

    def use_groups(self, **ground_truth):
        self.use_ground_truth(DenominatorTest.gt_for(*self.groups), **ground_truth)
        self.rewrite_experiment(lambda e: e["articles"].update(ids=list(self.article_ids)))

    def translate_both(self):
        self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), ok_response("9000002")))

    def weakened_in_political(self):
        change = {"verdict": "Weakened", "type": "a", "quote": "late"}
        return fact_transport({"9000002-001": lambda p: fact_output(p, {S1_FACT: change})})

    def test_each_domain_is_its_own_group_with_its_own_rate_and_the_gap_is_listed_per_approach(self):
        self.translate_both()
        report = self.gate(self.weakened_in_political())
        self.assertIn("淡化率：0/2（0.0%）", self.section(report, "### a × trad-to-en ・ 分組 general"))
        self.assertIn("淡化率：1/2（50.0%）", self.section(report, "### a × trad-to-en ・ 分組 china-political"))
        gap = self.section(report, "### political 與 general 的差距")
        self.assertIn("a × trad-to-en", gap)
        self.assertIn("+50.0 個百分點", gap)   # china-political 50.0% − general 0.0%

    def test_an_experiment_override_lists_articles_as_their_own_group_apart_from_their_domain(self):
        self.use_groups(group_overrides={"9000002": "q34"})
        self.translate_both()
        report = self.gate(self.weakened_in_political())
        self.assertIn("淡化率：1/2（50.0%）", self.section(report, "### a × trad-to-en ・ 分組 q34"))
        self.assertNotIn("分組 china-political", report)
        self.assertNotIn("+", self.section(report, "### political 與 general 的差距"))   # 9000002 已經不在 china-political：沒有差距可比

    def test_an_override_for_an_article_outside_the_experiment_is_rejected(self):
        self.use_groups(group_overrides={"9111111": "q34"})
        code, out, err = self.run_cli("judge", "--task", "fact", transport=FakeTransport())
        self.assertNotEqual(code, 0)
        self.assertIn("9111111", err)

    def test_the_gate_names_its_primary_judge_per_group_and_marks_an_unassigned_group_as_provisional(self):
        self.translate_both()
        self.use_judging(primary_judges={"china-political": "j1"})
        report = self.gate()
        self.assertIn("主 Judge j1（主 Judge 暫定）", self.section(report, "### a × trad-to-en ・ 分組 general"))
        self.assertNotIn("暫定", self.section(report, "### a × trad-to-en ・ 分組 china-political"))
        self.assertIn("主 Judge j1", self.section(report, "### a × trad-to-en ・ 分組 china-political"))

    def test_only_the_primary_judge_decides_the_gate_and_an_experiment_judge_is_reported_apart_from_it(self):
        other = JUDGE | {"id": "j2", "role": "experiment", "model": "gemini-3.1-other"}
        self.translate_both()
        self.use_judging(judges=(JUDGE, other))
        change = {"verdict": "Weakened", "type": "a", "quote": "late"}

        def reply(request):   # 只有 j2 的請求會判 Weakened：以 model 名分辨兩個 Judge
            payload = user_payload(request)
            model = request.url.split("/models/")[1].split(":")[0]
            output = fact_output(payload, {S1_FACT: change}) if model == "gemini-3.1-other" else fact_output(payload)
            return gemini_samples.ok(json.dumps(output, ensure_ascii=False))
        from fake_transport import FunctionTransport
        report = self.gate(FunctionTransport(reply))
        self.assertIn("淡化率：0/2（0.0%）", self.section(report, "### a × trad-to-en ・ 分組 general"))   # j1 說沒有問題

    def test_a_primary_judge_that_does_not_exist_or_is_not_primary_or_a_group_that_does_not_exist_is_rejected(self):
        other = JUDGE | {"id": "j2", "role": "experiment", "model": "gemini-3.1-other"}
        for primary in ({"general": "j9"}, {"general": "j2"}, {"sport": "j1"}):
            with self.subTest(str(primary)):
                self.use_judging(judges=(JUDGE, other), primary_judges=primary)
                code, out, err = self.run_cli("judge", "--task", "fact", transport=FakeTransport())
                self.assertNotEqual(code, 0)
                self.assertIn("primary_judges", err)


class ReportContextTest(GateCase):
    def test_the_report_opens_with_the_ground_truth_status_hash_and_the_articles_it_left_out(self):
        report = self.gate()
        self.assertIn("ground truth：draft", report)
        self.assertRegex(report, r"ground truth：draft[^\n]*[0-9a-f]{64}")
        self.assertIn("不在實驗內而略過 1 篇", report)

    def test_an_approved_ground_truth_is_reported_as_approved(self):
        self.use_ground_truth(status="approved")
        self.assertIn("ground truth：approved", self.gate())

    def test_there_is_no_gate_section_when_the_experiment_has_no_ground_truth(self):
        self.extra_experiment.pop("ground_truth")
        self.write_json(self.config_path, self.experiment())
        self.run_cli("judge", transport=fact_transport())
        report = self.report()
        self.assertNotIn("Gate", report)
        self.assertNotIn("ground truth", report)

    def test_fact_requests_do_not_change_the_mqm_section(self):
        report = self.gate(judge_args=())
        mqm = self.section(report, "### a × trad-to-en × Judge j1")
        self.assertIn("評完：5/5 個 Component", mqm)

    def test_the_report_needs_a_valid_ground_truth_like_the_judge_command(self):
        self.edited_ground_truth(lambda a: a["items"][0].update(anchor="預算嚴重超支"))
        self.run_cli("check")
        code, out, err = self.run_cli("report")
        self.assertNotEqual(code, 0)
        self.assertIn("anchor 不是逐字子字串", err)
