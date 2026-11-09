"""Must-retain fact 任務：ground truth 的驗證、每個敏感句的請求、verdict 的驗證與記錄、續跑與過期。
從 main(argv) 進入，transport 注入假的。只用虛構資料。"""
import copy
import hashlib
import json
import os
import shutil

import gemini_samples
from fake_transport import FakeTransport
from fixtures import GROUND_TRUTH, JUDGE, Workspace, ok_response
from judge_helpers import fact_output, fact_transport, judge_transport, system_text, user_payload

SENTENCE_COMPONENTS = ["9000001-001", "9000001-002"]


class FactCase(Workspace):
    def setUp(self):
        super().setUp()
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        self.use_judging()
        self.use_ground_truth()

    def facts(self, transport, *args):
        return self.run_cli("judge", "--task", "fact", *args, transport=transport)

    def calls(self):
        with open(os.path.join(self.out_dir, "judge-calls.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def fact_calls(self, component_id=None):
        return [c for c in self.calls() if c["task"] == "fact" and component_id in (None, c["component_id"])]

    def forget_calls(self):
        path = os.path.join(self.out_dir, "judge-calls.jsonl")
        if os.path.exists(path):
            os.remove(path)

    def edited_ground_truth(self, mutate) -> None:
        gt = copy.deepcopy(GROUND_TRUTH)
        mutate(gt["articles"][0])
        self.use_ground_truth(gt)


class GroundTruthValidationTest(FactCase):
    """驗證規則與 tools/render_ground_truth.py 相同；不合法就停止，不送請求，不寫任何 Judge 紀錄。"""

    def assert_rejected(self, needle):
        transport = FakeTransport()
        code, out, err = self.facts(transport)
        self.assertNotEqual(code, 0)
        self.assertIn(needle, err)
        self.assertEqual(transport.requests, [])
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "judge-calls.jsonl")))

    def test_a_component_id_that_is_not_in_the_article_is_rejected(self):
        self.edited_ground_truth(lambda a: a["items"][0].update(component_id="9000001-099"))
        self.assert_rejected("9000001-099")

    def test_an_anchor_that_is_not_a_verbatim_substring_is_rejected(self):
        self.edited_ground_truth(lambda a: a["items"][0].update(anchor="預算嚴重超支"))
        self.assert_rejected("anchor 不是逐字子字串")

    def test_a_component_that_is_not_translated_cannot_hold_a_sensitive_sentence(self):
        self.edited_ground_truth(lambda a: a["items"][0].update(component_id="9000001-004", anchor="embed"))
        self.assert_rejected("9000001-004")

    def test_a_kind_or_domain_outside_the_allowed_lists_is_rejected(self):
        self.edited_ground_truth(lambda a: a["items"][0]["must_retain"][0].update(kind="rumour"))
        self.assert_rejected("kind")
        self.edited_ground_truth(lambda a: a.update(domain="sport"))
        self.assert_rejected("domain")

    def test_an_item_without_facts_the_same_article_twice_and_the_same_fact_twice_are_rejected(self):
        self.edited_ground_truth(lambda a: a["items"][0].update(must_retain=[]))
        self.assert_rejected("must_retain")
        gt = copy.deepcopy(GROUND_TRUTH)
        gt["articles"].append(gt["articles"][0])
        self.use_ground_truth(gt)
        self.assert_rejected("9000001")
        self.edited_ground_truth(lambda a: a["items"][0]["must_retain"].append({"kind": "act", "fact": "工程延誤半年"}))
        self.assert_rejected("重覆")

    def test_an_unreadable_or_misspelt_file_is_rejected_without_echoing_its_content(self):
        path = self.use_ground_truth()
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assert_rejected(path)
        gt = copy.deepcopy(GROUND_TRUTH)
        gt["articles"][0]["items"][0]["must_retian"] = []
        self.use_ground_truth(gt)
        self.assert_rejected("must_retian")

    def test_articles_in_the_ground_truth_but_not_in_the_experiment_are_ignored(self):
        code, out, err = self.facts(fact_transport())
        self.assertEqual(code, 0, err)   # GROUND_TRUTH 內有一篇 9999999，不在實驗內

    def test_the_same_checks_run_for_report_and_dry_run(self):
        self.edited_ground_truth(lambda a: a["items"][0].update(anchor="預算嚴重超支"))
        code, out, err = self.run_cli("judge", "--task", "fact", "--dry-run", transport=FakeTransport())
        self.assertNotEqual(code, 0)
        self.run_cli("check")
        code, out, err = self.run_cli("report")
        self.assertNotEqual(code, 0)
        self.assertIn("anchor 不是逐字子字串", err)

    def test_the_facts_task_needs_a_ground_truth_in_the_experiment_config(self):
        self.extra_experiment.pop("ground_truth")
        self.write_json(self.config_path, self.experiment())
        transport = FakeTransport()
        code, out, err = self.facts(transport)
        self.assertNotEqual(code, 0)
        self.assertIn("ground_truth", err)
        self.assertEqual(transport.requests, [])


class RequestTest(FactCase):
    def test_each_sensitive_component_is_sent_twice_with_its_facts_and_nothing_else_is_sent(self):
        transport = fact_transport()
        code, out, err = self.facts(transport)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(user_payload(r)["component"]["id"] for r in transport.requests), sorted(SENTENCE_COMPONENTS * 2))
        self.assertEqual(sorted((c["component_id"], c["call_index"]) for c in self.fact_calls()),
                         sorted((cid, n) for cid in SENTENCE_COMPONENTS for n in (1, 2)))
        self.assertNotIn("虛構", out)

    def test_the_request_carries_the_sentence_anchor_the_facts_with_ids_and_kinds_and_the_translation(self):
        transport = fact_transport()
        self.facts(transport)
        payload = next(user_payload(r) for r in transport.requests if user_payload(r)["component"]["id"] == "9000001-001")
        self.assertEqual(payload["direction"], "trad-to-en")
        self.assertEqual(payload["component"]["source"], "翻新工程延誤半年，預算超支。")
        self.assertEqual(payload["component"]["translation"], "The renovation is six months late and over budget.")
        self.assertEqual(len(payload["sentences"]), 1)
        sentence = payload["sentences"][0]
        self.assertEqual(sentence["anchor"], "預算超支")
        self.assertEqual([(f["kind"], f["fact"]) for f in sentence["facts"]], [("detail", "工程延誤半年"), ("act", "預算超支")])
        self.assertEqual(len({f["id"] for f in sentence["facts"]}), 2)

    def test_the_prompt_defines_the_verdicts_the_weakening_types_amplification_and_the_compound_rule(self):
        transport = fact_transport()
        self.facts(transport)
        prompt = system_text(transport.requests[0])
        for term in ("Present", "Weakened", "Absent", "(a)", "(f)", "Amplification", "verbatim", "quote", "compound"):
            self.assertIn(term, prompt)
        self.assertIn("any", prompt.lower())

    def test_a_component_missing_from_the_translation_is_not_sent_and_its_facts_are_not_judged_at_all(self):
        shutil.rmtree(self.data_dir)
        translation = json.loads(ok_response().body)["candidates"][0]["content"]["parts"][0]["text"]
        gone = json.loads(translation)
        gone["components"] = [c for c in gone["components"] if c["id"] != "9000001-002"]
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(gone, ensure_ascii=False))))
        transport = fact_transport()
        self.facts(transport)
        self.assertEqual({user_payload(r)["component"]["id"] for r in transport.requests}, {"9000001-001"})

    def test_the_task_flag_selects_what_runs_and_the_default_runs_both(self):
        transport = fact_transport()
        self.run_cli("judge", "--task", "mqm", transport=transport)
        self.assertTrue(all("sentences" not in user_payload(r) for r in transport.requests))
        self.assertEqual(len(transport.requests), 10)
        self.forget_calls()
        transport = fact_transport()
        self.run_cli("judge", transport=transport)
        self.assertEqual(len(transport.requests), 14)   # 5 個 Component × 2 + 2 個敏感 Component × 2

    def test_the_dry_run_counts_the_fact_requests_and_sends_nothing(self):
        del os.environ["GEMINI_API_KEY"]
        transport = FakeTransport()
        code, out, err = self.facts(transport, "--dry-run")
        self.assertEqual((code, transport.requests), (0, []))
        self.assertIn("請求數：4", out)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "judge-calls.jsonl")))

    def test_the_same_family_rule_applies_to_the_fact_task(self):
        same_family = JUDGE | {"family": "gemini"}
        self.use_judging(judges=(same_family,))
        transport = FakeTransport()
        code, out, err = self.facts(transport)
        self.assertNotEqual(code, 0)
        self.assertIn("同家族", err)
        self.assertEqual(transport.requests, [])

    def test_the_mqm_calls_and_the_fact_calls_do_not_share_an_identity(self):
        self.run_cli("judge", transport=fact_transport())
        self.assertEqual(len(self.calls()), 14)
        transport = fact_transport()
        self.run_cli("judge", transport=transport)   # 都有紀錄了：略過
        self.assertEqual(transport.requests, [])


class VerdictRecordingTest(FactCase):
    FACT = "預算超支"

    def record(self, **change):
        """第 1 次呼叫的 001 事實「預算超支」用 change 覆寫；回傳 001 的第 1 次紀錄。"""
        self.forget_calls()
        self.facts(fact_transport({("9000001-001", 1): lambda p: fact_output(p, {self.FACT: change})}))
        return next(c for c in self.fact_calls("9000001-001") if c["call_index"] == 1)

    def verdict_of(self, call, fact_text="預算超支"):
        return [v for v in call["verdicts"] if v["fact"] == fact_key(fact_text)]

    def test_a_valid_verdict_is_recorded_with_its_quote_type_and_reason(self):
        call = self.record(verdict="Weakened", type="e", quote="over budget", reason="虛構的理由")
        self.assertEqual((call["outcome"], call["invalid"]), ("ok", []))
        self.assertEqual(len(call["verdicts"]), 2)
        verdict = self.verdict_of(call)[0]
        self.assertEqual((verdict["verdict"], verdict["weakening_type"], verdict["quote"]), ("Weakened", "e", "over budget"))

    def test_a_quote_that_is_not_a_verbatim_substring_voids_the_verdict_and_keeps_the_raw_item_for_review(self):
        call = self.record(verdict="Present", quote="the budget was exceeded")
        self.assertEqual(self.verdict_of(call), [])
        self.assertEqual(len(call["verdicts"]), 1)   # 另一條事實不受影響
        invalid = call["invalid"]
        self.assertEqual((len(invalid), invalid[0]["kind"], invalid[0]["fact"]), (1, "verdict", fact_key(self.FACT)))
        self.assertEqual(json.loads(invalid[0]["raw"])["quote"], "the budget was exceeded")

    def test_present_and_weakened_need_a_quote_but_absent_does_not(self):
        for verdict in ("Present", "Weakened"):
            with self.subTest(verdict):
                call = self.record(verdict=verdict, type="e" if verdict == "Weakened" else None, quote="")
                self.assertEqual(self.verdict_of(call), [])
                self.assertEqual(len(call["invalid"]), 1)
        call = self.record(verdict="Absent", quote="")
        self.assertEqual((self.verdict_of(call)[0]["verdict"], call["invalid"]), ("Absent", []))

    def test_a_type_written_with_parentheses_or_in_capitals_is_read_as_the_same_type(self):
        for written, expected in (("(e)", "e"), ("E", "e"), ("Amplification", "amplification")):
            with self.subTest(written):
                call = self.record(verdict="Present" if expected == "amplification" else "Weakened", type=written, quote="over budget")
                self.assertEqual(self.verdict_of(call)[0]["weakening_type"], expected)

    def test_quotes_are_compared_after_squashing_whitespace_like_findings(self):
        call = self.record(verdict="Present", quote="six months   late")
        self.assertEqual(self.verdict_of(call)[0]["verdict"], "Present")

    def test_a_verdict_a_type_or_a_shape_outside_the_allowed_lists_is_invalid(self):
        for change in ({"verdict": "Maybe"}, {"verdict": "Weakened", "type": "g"}, {"verdict": "Weakened", "type": "amplification"},
                       {"verdict": "present"}, {"verdict": 3}, {"reason": 5}):
            with self.subTest(str(change)):
                call = self.record(**change)
                self.assertEqual(self.verdict_of(call), [])
                self.assertEqual(len(call["invalid"]), 1)

    def test_amplification_goes_with_a_present_fact_and_a_type_may_be_left_out_of_a_weakened_one(self):
        call = self.record(verdict="Present", type="amplification")
        self.assertEqual(self.verdict_of(call)[0]["weakening_type"], "amplification")
        call = self.record(verdict="Weakened", type=None, quote="over budget")
        self.assertEqual((self.verdict_of(call)[0]["verdict"], self.verdict_of(call)[0]["weakening_type"]), ("Weakened", None))

    def test_a_fact_the_model_left_out_a_duplicate_verdict_and_an_unknown_fact_id_are_all_invalid(self):
        def output(payload):
            first = fact_output(payload)["verdicts"][0]   # 001 有兩條事實：第一條回兩次，第二條沒回，另外回一個不認識的 id
            return {"verdicts": [first, first, {"fact_id": "F99", "verdict": "Present", "quote": "x", "reason": "x"}]}
        self.facts(fact_transport({("9000001-001", 1): output}))
        call = next(c for c in self.fact_calls("9000001-001") if c["call_index"] == 1)
        self.assertEqual(call["verdicts"], [])   # 重覆的 verdict 兩個都不用；沒回的事實沒有 verdict
        self.assertEqual(sorted(i["fact"] or "" for i in call["invalid"]), sorted(["", fact_key("工程延誤半年"), fact_key("預算超支")]))

    def test_an_output_that_is_not_the_expected_shape_is_malformed_and_a_block_is_recorded_as_such(self):
        for output, expected in (({"verdicts": "no"}, "malformed"), ({"findings": []}, "malformed"), (gemini_samples.prompt_blocked(), "block")):
            with self.subTest(expected):
                self.forget_calls()
                self.facts(fact_transport({("9000001-001", 1): output}))
                call = next(c for c in self.fact_calls("9000001-001") if c["call_index"] == 1)
                self.assertEqual((call["outcome"], call["verdicts"]), (expected, []))

    def test_a_call_record_names_the_task_the_prompt_the_input_and_the_raw_response(self):
        self.facts(fact_transport())
        call = self.fact_calls("9000001-001")[0]
        self.assertEqual((call["task"], call["judge"], call["article_id"]), ("fact", "j1", "9000001"))
        self.assertRegex(call["prompt_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(call["input_hash"], r"^[0-9a-f]{64}$")
        self.assertIn("raw_response", call)


class ResumeTest(FactCase):
    def test_a_rerun_skips_requests_that_already_have_a_record(self):
        self.facts(fact_transport())
        transport = fact_transport()
        code, out, _ = self.facts(transport)
        self.assertEqual((code, transport.requests), (0, []))
        self.assertIn("已有紀錄而略過：4", out)

    def test_an_error_is_redone_only_with_redo_errors_and_replaces_the_old_record(self):
        self.facts(fact_transport({("9000001-001", 1): gemini_samples.client_error()}))
        self.assertEqual([c["outcome"] for c in self.fact_calls("9000001-001")], ["error", "ok"])
        transport = fact_transport()
        self.facts(transport)
        self.assertEqual(transport.requests, [])
        self.facts(transport, "--redo-errors")
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(sorted((c["call_index"], c["outcome"]) for c in self.fact_calls("9000001-001")), [(1, "ok"), (2, "ok")])

    def test_changing_a_fact_text_makes_the_old_record_stale_and_only_that_component_is_judged_again(self):
        self.facts(fact_transport())
        self.edited_ground_truth(lambda a: a["items"][1]["must_retain"][0].update(fact="不滿的是市民本人"))
        transport = fact_transport()
        self.facts(transport)
        self.assertEqual([user_payload(r)["component"]["id"] for r in transport.requests], ["9000001-002"] * 2)
        self.assertEqual(len(self.fact_calls()), 4)   # 舊的被換掉，不是疊上去


def fact_key(fact_text: str) -> str:
    """事實的 key 的最後一段：事實文字的 SHA-256（spec：文章 id、Component id 和事實文字的 hash；前兩段在紀錄的其他欄位）。"""
    return hashlib.sha256(fact_text.encode("utf-8")).hexdigest()
