"""judge：MQM 任務。每個要翻譯的 Component 由 Judge 評兩次；片段逐字驗證；續跑；同家族規則。從 main(argv) 進入，transport 注入假的。"""
import json
import os
import shutil
import unittest

import gemini_samples
from fake_transport import FakeTransport
from fixtures import FAKE_KEY, JUDGE, Workspace, ok_response, source_article
from judge_helpers import NO_PROBLEMS, finding, judge_transport, system_text, user_payload

COMPONENTS = ["title", "9000001-001", "9000001-002", "9000001-003", "tags"]   # 要翻譯的：title、highlight、paragraph、image、tags


class JudgeCase(Workspace):
    def setUp(self):
        super().setUp()
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        self.use_judging()

    def judge(self, transport, *args):
        code, out, err = self.run_cli("judge", *args, transport=transport)
        return code, out, err

    def calls(self):
        with open(os.path.join(self.out_dir, "judge-calls.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def calls_of(self, component_id):
        return [c for c in self.calls() if c["component_id"] == component_id]

    def forget_calls(self):
        """清掉已有的 Judge 紀錄：同一個測試要換一組回應重跑，有紀錄的請求會被略過。"""
        path = os.path.join(self.out_dir, "judge-calls.jsonl")
        if os.path.exists(path):
            os.remove(path)


class RequestTest(JudgeCase):
    def test_every_translatable_component_is_judged_twice_by_two_independent_requests(self):
        transport = judge_transport()
        code, out, err = self.judge(transport)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(transport.requests), 10)
        ids = [user_payload(r)["component"]["id"] for r in transport.requests]
        self.assertEqual(sorted(ids), sorted(COMPONENTS * 2))
        self.assertEqual(sorted((c["component_id"], c["call_index"]) for c in self.calls()),
                         sorted((cid, n) for cid in COMPONENTS for n in (1, 2)))
        self.assertNotIn("虛構", out)   # 終端只印數量

    def test_the_request_has_temperature_zero_json_output_and_the_key_in_a_header(self):
        transport = judge_transport()
        self.judge(transport)
        body = json.loads(transport.requests[0].body)
        self.assertEqual(body["generationConfig"]["temperature"], 0)
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("x-goog-api-key", transport.requests[0].headers)

    def test_the_prompt_defines_every_dimension_severity_softening_amplification_legal_status_and_source_defect(self):
        transport = judge_transport()
        self.judge(transport)
        prompt = system_text(transport.requests[0])
        for term in ("Accuracy", "Terminology", "Fluency", "Fidelity-of-force", "Locale & Format", "minor", "major", "critical",
                     "(a)", "(f)", "Amplification", "Legal status", "source_defects", "verbatim"):
            self.assertIn(term, prompt)
        self.assertIn("not softening", prompt.lower())   # Term List 的慣用譯法不算 Softening

    def test_the_request_carries_the_component_its_translation_the_direction_and_one_neighbour_each_side(self):
        transport = judge_transport()
        self.judge(transport)
        article = source_article()
        middle = next(user_payload(r) for r in transport.requests if user_payload(r)["component"]["id"] == "9000001-002")
        self.assertEqual(middle["direction"], "trad-to-en")
        self.assertIn("register", middle["requirements"].lower())
        self.assertEqual(middle["component"]["source"], article["components"][1]["text"])
        self.assertEqual(middle["component"]["translation"], "Resident Chan Fictional said the delay left him deeply dissatisfied.")
        self.assertEqual(middle["context_before"]["id"], "9000001-001")
        self.assertEqual(middle["context_after"]["id"], "9000001-003")   # code 不翻譯，不算前後文
        self.assertIn("source", middle["context_before"])
        self.assertIn("translation", middle["context_before"])

    def test_the_first_and_last_components_have_no_neighbour_on_one_side(self):
        transport = judge_transport()
        self.judge(transport)
        payloads = {user_payload(r)["component"]["id"]: user_payload(r) for r in transport.requests}
        self.assertIsNone(payloads["title"]["context_before"])
        self.assertEqual(payloads["title"]["context_after"]["id"], "9000001-001")
        self.assertIsNone(payloads["tags"]["context_after"])
        self.assertEqual(payloads["tags"]["context_before"]["id"], "9000001-003")

    def test_only_the_term_list_entries_that_occur_in_the_component_are_given(self):
        self.use_term_list({"terms": [
            {"source": "陳虛構", "category": "person", "translations": {"trad-to-en": ["Chan Fictional"]}},
            {"source": "從未出現的詞", "category": "place", "translations": {"trad-to-en": ["Never Mentioned"]}}]})
        shutil.rmtree(self.data_dir)
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        transport = judge_transport()
        self.judge(transport)
        by_id = {user_payload(r)["component"]["id"]: user_payload(r) for r in transport.requests}
        self.assertEqual(by_id["9000001-002"]["term_list"], ["陳虛構 -> Chan Fictional"])
        self.assertEqual(by_id["9000001-001"]["term_list"], [])

    def test_a_call_record_names_the_run_the_judge_the_prompt_and_the_tokens(self):
        self.judge(judge_transport())
        call = self.calls_of("9000001-001")[0]
        self.assertEqual((call["article_id"], call["approach"], call["direction"], call["judge"], call["task"], call["outcome"]),
                         ("9000001", "a", "trad-to-en", "j1", "mqm", "ok"))
        self.assertRegex(call["prompt_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual((call["input_tokens"], call["output_tokens"]), (120, 80))
        self.assertIn("raw_response", call)

    def test_only_ok_runs_are_judged(self):
        shutil.rmtree(self.data_dir)
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked()))
        transport = judge_transport()
        code, out, err = self.judge(transport)
        self.assertEqual((code, transport.requests), (0, []))

    def test_check_does_not_have_to_run_first(self):
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "components.jsonl")))
        self.assertEqual(self.judge(judge_transport())[0], 0)

    def test_the_run_limit_restricts_mqm_to_the_first_runs_of_an_approach(self):
        shutil.rmtree(self.data_dir)
        self.runs = 2
        self.use_judging(mqm_run_limit={"a": 1})
        self.run_cli("translate", transport=FakeTransport(ok_response(), ok_response()))
        transport = judge_transport()
        self.judge(transport)
        self.assertEqual(len(transport.requests), 10)   # 只有第 1 個 Run
        self.assertEqual({c["run_index"] for c in self.calls()}, {1})


class DryRunTest(JudgeCase):
    def test_the_dry_run_counts_requests_per_judge_sends_nothing_and_needs_no_key(self):
        del os.environ["GEMINI_API_KEY"]
        transport = FakeTransport()
        code, out, _ = self.judge(transport, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(transport.requests, [])
        self.assertIn("請求數：10", out)
        self.assertIn("Judge j1：請求數 10", out)
        self.assertIn("https://generativelanguage.googleapis.com", out)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "judge-calls.jsonl")))


class VerbatimValidationTest(JudgeCase):
    def one_finding_call(self, item):
        self.judge(judge_transport({("9000001-001", 1): {"findings": [item], "source_defects": []}}))
        return self.calls_of("9000001-001")[0]

    def test_a_finding_whose_spans_are_verbatim_is_kept(self):
        call = self.one_finding_call(finding())
        self.assertEqual((call["outcome"], len(call["findings"]), call["invalid"]), ("ok", 1, []))
        self.assertEqual(call["findings"][0]["severity"], "major")

    def test_spans_are_compared_after_squashing_runs_of_whitespace(self):
        call = self.one_finding_call(finding(source_span="預算 超支", target_span="over   budget"))
        self.assertEqual(len(call["findings"]), 1)

    def test_a_span_that_is_not_a_verbatim_substring_is_dropped_counted_and_kept_for_review(self):
        for name, item in (("source", finding(source_span="預算嚴重超支")), ("target", finding(target_span="way over budget")),
                           ("case differs", finding(target_span="Over Budget"))):
            with self.subTest(name):
                self.forget_calls()
                call = self.one_finding_call(item)
                self.assertEqual((call["outcome"], call["findings"]), ("ok", []))
                self.assertEqual(len(call["invalid"]), 1)
                self.assertEqual(call["invalid"][0]["kind"], "finding")
                self.assertEqual(call["invalid"][0]["raw"], item)   # 原文保留供審閱

    def test_an_empty_target_span_is_fine_when_the_source_span_is_verbatim_but_two_empty_spans_are_not(self):
        self.assertEqual(len(self.one_finding_call(finding(target_span="", subtype="omission"))["findings"]), 1)
        self.forget_calls()
        self.assertEqual(len(self.one_finding_call(finding(source_span="", target_span=""))["invalid"]), 1)

    def test_a_dimension_or_severity_outside_the_allowed_lists_or_a_wrong_shape_is_invalid(self):
        bad = [finding(dimension="Style"), finding(severity="catastrophic"), finding(subtype=""), {"dimension": "Accuracy"}, "not an object",
               finding() | {"severity": 5}]
        for item in bad:
            with self.subTest(str(item)[:40]):
                self.forget_calls()
                call = self.one_finding_call(item)
                self.assertEqual((call["findings"], len(call["invalid"])), ([], 1))

    def test_valid_and_invalid_items_in_one_output_are_separated(self):
        self.judge(judge_transport({("9000001-001", 1): {"findings": [finding(), finding(source_span="不存在"), finding(dimension="Fluency", subtype="grammar",
                                                                                                                       source_span="", target_span="six months late")],
                                                          "source_defects": []}}))
        call = self.calls_of("9000001-001")[0]
        self.assertEqual((len(call["findings"]), len(call["invalid"])), (2, 1))

    def test_source_defects_are_validated_against_the_source_text_only(self):
        good = {"source_span": "延誤半年", "issue": "虛構的問題"}
        bad = {"source_span": "不存在的片段", "issue": "虛構的問題"}
        self.judge(judge_transport({("9000001-001", 1): {"findings": [], "source_defects": [good, bad, {"source_span": "延誤半年"}]}}))
        call = self.calls_of("9000001-001")[0]
        self.assertEqual(call["source_defects"], [good])
        self.assertEqual([i["kind"] for i in call["invalid"]], ["source_defect", "source_defect"])

    def test_a_structure_that_is_not_findings_and_source_defects_makes_the_call_malformed(self):
        for name, output in (("no findings", {"source_defects": []}), ("findings not a list", {"findings": "none", "source_defects": []}),
                             ("source_defects not a list", {"findings": [], "source_defects": 5}), ("not an object", [])):
            with self.subTest(name):
                self.forget_calls()
                self.judge(judge_transport({("9000001-001", 1): output if isinstance(output, dict) else gemini_samples.ok(json.dumps(output))}))
                call = self.calls_of("9000001-001")[0]
                self.assertEqual(call["outcome"], "malformed")
                self.assertEqual(call["findings"], [])

    def test_a_missing_source_defects_list_is_fine(self):
        self.judge(judge_transport({("9000001-001", 1): {"findings": [finding()]}}))
        self.assertEqual(self.calls_of("9000001-001")[0]["outcome"], "ok")


class FailureTest(JudgeCase):
    def test_a_blocked_or_refused_judge_call_is_recorded_with_its_reason_and_not_retried(self):
        transport = judge_transport({("9000001-001", 1): gemini_samples.prompt_blocked(), ("9000001-001", 2): gemini_samples.refusal()})
        self.judge(transport)
        self.assertEqual(len([r for r in transport.requests if user_payload(r)["component"]["id"] == "9000001-001"]), 2)
        first, second = sorted(self.calls_of("9000001-001"), key=lambda c: c["call_index"])
        self.assertEqual((first["outcome"], first["reason"]), ("block", "prompt:PROHIBITED_CONTENT"))
        self.assertEqual(second["outcome"], "refusal")

    def test_a_transient_failure_is_retried_and_exhausted_retries_are_an_error(self):
        transport = judge_transport({("9000001-001", 1): gemini_samples.rate_limited()})
        self.judge(transport)
        first = sorted(self.calls_of("9000001-001"), key=lambda c: c["call_index"])[0]
        self.assertEqual(first["outcome"], "ok")   # 重試一次就成功了
        self.assertEqual([a["http_status"] for a in first["attempts"]], [429, 200])

    def test_a_missing_key_stops_before_any_request_and_names_only_the_variable(self):
        del os.environ["GEMINI_API_KEY"]
        transport = FakeTransport()
        code, _, err = self.judge(transport)
        self.assertNotEqual(code, 0)
        self.assertIn("GEMINI_API_KEY", err)
        self.assertEqual(transport.requests, [])


class ResumeTest(JudgeCase):
    def test_rerunning_sends_nothing_and_only_missing_calls_are_sent(self):
        self.judge(judge_transport(), "--limit", "4")
        self.assertEqual(len(self.calls()), 4)
        transport = judge_transport()
        self.judge(transport)
        self.assertEqual(len(transport.requests), 6)
        self.assertEqual(len(self.calls()), 10)
        transport = judge_transport()
        self.assertEqual(self.judge(transport)[0], 0)
        self.assertEqual(transport.requests, [])

    def test_redo_errors_replaces_error_calls_only(self):
        self.judge(judge_transport({("9000001-001", 1): gemini_samples.unknown_format(), ("9000001-001", 2): gemini_samples.prompt_blocked()}))
        self.assertEqual(sorted(c["outcome"] for c in self.calls_of("9000001-001")), ["block", "error"])
        transport = judge_transport()
        self.judge(transport, "--redo-errors")
        self.assertEqual(len(transport.requests), 1)   # 只有 error 的那一個；block 不重做
        self.assertEqual(sorted(c["outcome"] for c in self.calls_of("9000001-001")), ["block", "ok"])
        self.assertEqual(len(self.calls()), 10)

    def test_article_and_limit_options_narrow_the_requests(self):
        transport = judge_transport()
        self.judge(transport, "--limit", "3")
        self.assertEqual(len(transport.requests), 3)
        code, _, err = self.judge(judge_transport(), "--article", "8000000")
        self.assertNotEqual(code, 0)


class SameFamilyTest(JudgeCase):
    def test_a_judge_of_the_same_family_as_an_approach_is_refused_before_any_request(self):
        self.use_judging(judges=({**JUDGE, "family": "gemini"},))
        transport = judge_transport()
        code, _, err = self.judge(transport)
        self.assertNotEqual(code, 0)
        self.assertIn("同家族", err)
        self.assertIn("j1", err)
        self.assertEqual(transport.requests, [])
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "judge-calls.jsonl")))

    def test_an_exception_with_a_reason_for_exactly_that_pair_lets_it_run(self):
        self.use_judging(judges=({**JUDGE, "family": "gemini"},), same_family_exceptions=[{"approach": "a", "judge": "j1", "reason": "測試用的例外"}])
        transport = judge_transport()
        code, _, err = self.judge(transport)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(transport.requests), 10)

    def test_an_exception_for_another_pair_does_not_help(self):
        self.use_judging(judges=({**JUDGE, "family": "gemini"}, {**JUDGE, "id": "j2", "family": "claude"}),
                         same_family_exceptions=[{"approach": "a", "judge": "j2", "reason": "不是這一對"}])
        self.assertNotEqual(self.judge(judge_transport())[0], 0)

    def test_an_exception_without_a_reason_or_naming_something_that_does_not_exist_is_rejected(self):
        for name, exception in (("blank reason", {"approach": "a", "judge": "j1", "reason": "  "}),
                                ("no reason", {"approach": "a", "judge": "j1"}),
                                ("unknown approach", {"approach": "zz", "judge": "j1", "reason": "x"}),
                                ("unknown judge", {"approach": "a", "judge": "zz", "reason": "x"})):
            with self.subTest(name):
                self.use_judging(same_family_exceptions=[exception])
                transport = FakeTransport()
                code, _, _ = self.run_cli("judge", transport=transport)
                self.assertNotEqual(code, 0)
                self.assertEqual(transport.requests, [])


class ConfigTest(JudgeCase):
    def test_invalid_judge_settings_are_rejected(self):
        for name, judge, needle in (
            ("temperature other than zero", {**JUDGE, "temperature": 0.7}, "temperature"),
            ("per-component mode", {**JUDGE, "request_mode": "per-component"}, "article-json"),
            ("unknown role", {**JUDGE, "role": "boss"}, "role"),
            ("no role", {k: v for k, v in JUDGE.items() if k != "role"}, "role"),
            ("same id as an approach", {**JUDGE, "id": "a"}, "id"),
            ("another provider's key variable", {**JUDGE, "api_key_env": "OPENROUTER_API_KEY"}, "GEMINI_API_KEY"),
        ):
            with self.subTest(name):
                self.use_judging(judges=(judge,))
                code, _, err = self.run_cli("judge", "--dry-run", transport=FakeTransport())
                self.assertNotEqual(code, 0)
                self.assertIn(needle, err)

    def test_two_judges_with_the_same_id_are_rejected(self):
        self.use_judging(judges=(JUDGE, JUDGE))
        code, _, err = self.run_cli("judge", "--dry-run", transport=FakeTransport())
        self.assertNotEqual(code, 0)
        self.assertIn("重覆", err)

    def test_an_experiment_without_judging_has_nothing_to_judge(self):
        del self.extra_experiment["judging"]
        self.write_json(self.config_path, self.experiment())
        code, _, err = self.run_cli("judge", transport=FakeTransport())
        self.assertNotEqual(code, 0)
        self.assertIn("judging", err)


class KeyLeakTest(JudgeCase):
    def test_the_key_never_reaches_a_judge_output_file_even_when_the_server_echoes_it(self):
        leaking = gemini_samples.with_fields(gemini_samples.ok(json.dumps(NO_PROBLEMS)), echo=f"you sent {FAKE_KEY}")
        self.judge(judge_transport({c: leaking for c in COMPONENTS}))
        self.assertNotIn(FAKE_KEY, self.all_output_text())


if __name__ == "__main__":
    unittest.main()
