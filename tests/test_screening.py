"""全文篩查：每個 Run 兩個請求、條目的逐字驗證、一致性、進審閱清單的必看、不改變 Gate 和分數。從 main(argv) 進入，transport 注入假的。只用虛構資料。"""
import json
import os
import shutil
from pathlib import Path

import gemini_samples
from fake_transport import FakeTransport
from gate_helpers import S1_FACT, WEAK, ReviewCase, combined_transport, translation_without
from judge_helpers import fact_output, finding, screen_item, screen_transport, system_text, user_payload

SOURCE_001 = "翻新工程延誤半年，預算超支。"


class ScreenCase(ReviewCase):
    def setUp(self):
        super().setUp()
        self.use_judging(screening=True)

    def screen(self, outputs=None, *args):
        transport = screen_transport(outputs)
        code, out, err = self.run_cli("judge", "--task", "screen", *args, transport=transport)
        self.assertEqual(code, 0, err)
        return transport

    def calls(self):
        text = Path(self.out_dir, "judge-calls.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def screen_calls(self):
        return sorted((c for c in self.calls() if c["task"] == "screen"), key=lambda c: c["call_index"])

    def review_part(self, heading="必看") -> str:
        self.assertEqual(self.run_cli("check")[0], 0)
        return self.part(self.review_text(), heading)


class RequestTest(ScreenCase):
    def test_each_run_is_screened_twice_with_the_whole_article_and_its_translation(self):
        transport = self.screen()
        self.assertEqual(len(transport.requests), 2)
        payload = user_payload(transport.requests[0])
        self.assertEqual(payload["direction"], "trad-to-en")
        ids = [c["id"] for c in payload["components"]]
        self.assertEqual(ids, ["title", "9000001-001", "9000001-002", "9000001-003", "tags"])
        first = payload["components"][1]
        self.assertEqual((first["source"], first["translation"]), (SOURCE_001, "The renovation is six months late and over budget."))
        self.assertEqual([(c["task"], c["component_id"], c["call_index"]) for c in self.screen_calls()], [("screen", "*", 1), ("screen", "*", 2)])

    def test_the_prompt_defines_the_verdicts_the_types_and_the_verbatim_rule(self):
        prompt = system_text(self.screen().requests[0])
        for term in ("Weakened", "Absent", "Amplification", "(a)", "(f)", "verbatim", "ONE component", "key facts"):
            self.assertIn(term, prompt)

    def test_a_missing_component_is_sent_with_a_null_translation(self):
        shutil.rmtree(self.data_dir)
        self.run_cli("translate", transport=FakeTransport(translation_without("9000001-002")))
        components = {c["id"]: c for c in user_payload(self.screen().requests[0])["components"]}
        self.assertIsNone(components["9000001-002"]["translation"])

    def test_screening_is_part_of_judge_by_default_but_the_config_can_turn_it_off_and_the_flag_overrides_that(self):
        transport = combined_transport()
        self.run_cli("judge", transport=transport)
        self.assertEqual(sum(1 for r in transport.requests if "components" in user_payload(r)), 2)
        self.use_judging(screening=False)
        self.forget_calls()
        transport = combined_transport()
        self.run_cli("judge", transport=transport)
        self.assertEqual(sum(1 for r in transport.requests if "components" in user_payload(r)), 0)
        transport = combined_transport()
        self.run_cli("judge", "--task", "screen", transport=transport)   # 明講要跑就跑
        self.assertEqual(len(transport.requests), 2)

    def test_the_dry_run_counts_the_screening_requests_and_the_whole_article_in_the_estimate(self):
        del os.environ["GEMINI_API_KEY"]
        code, out, err = self.run_cli("judge", "--task", "screen", "--dry-run", transport=FakeTransport())
        self.assertEqual(code, 0, err)
        self.assertIn("請求數：2", out)

    def test_a_rerun_skips_requests_that_already_have_a_record(self):
        self.screen()
        transport = self.screen()
        self.assertEqual(transport.requests, [])


class ValidationTest(ScreenCase):
    def one_call(self, *items):
        self.forget_calls()
        self.screen({1: {"items": list(items)}})
        return self.screen_calls()[0]

    def test_a_valid_item_is_kept_with_the_component_its_source_span_is_in(self):
        call = self.one_call(screen_item())
        self.assertEqual((call["outcome"], call["invalid"]), ("ok", []))
        kept = call["screenings"][0]
        self.assertEqual((kept["verdict"], kept["weakening_type"], kept["component_id"]), ("Weakened", "c", "9000001-001"))

    def test_absent_may_leave_the_target_span_empty_but_weakened_and_amplification_may_not(self):
        self.assertEqual(len(self.one_call(screen_item(verdict="Absent", type=None, target_span=""))["screenings"]), 1)
        for verdict in ("Weakened", "Amplification"):
            call = self.one_call(screen_item(verdict=verdict, type=None, target_span=""))
            self.assertEqual((call["screenings"], len(call["invalid"])), ([], 1), verdict)

    def test_spans_that_are_not_verbatim_a_wrong_verdict_or_type_and_a_wrong_shape_are_dropped_and_kept_for_review(self):
        bad = [screen_item(source_span="不存在的片段"), screen_item(target_span="not in the translation"), screen_item(verdict="Maybe"),
               screen_item(verdict="Weakened", type="amplification"), screen_item(verdict="Amplification", type="a"), screen_item(source_span=""),
               "not an object", screen_item() | {"reason": 5}]
        for item in bad:
            with self.subTest(str(item)[:50]):
                call = self.one_call(item)
                self.assertEqual((call["outcome"], call["screenings"], len(call["invalid"])), ("ok", [], 1))
                self.assertEqual(call["invalid"][0]["kind"], "screening")
        call = self.one_call(screen_item(target_span="not in the translation"))
        self.assertEqual(json.loads(call["invalid"][0]["raw"])["target_span"], "not in the translation")   # 原文保留

    def test_a_source_span_must_sit_inside_one_component_and_spaces_are_ignored(self):
        self.assertEqual(len(self.one_call(screen_item(source_span="延誤 半年"))["screenings"]), 1)
        crossing = self.one_call(screen_item(source_span="預算超支。市民"))   # 跨過兩個 Component 的邊界
        self.assertEqual(len(crossing["invalid"]), 1)

    def test_an_output_that_is_not_the_expected_shape_is_malformed_and_a_block_is_recorded_as_such(self):
        for output, expected in (({"items": "no"}, "malformed"), ({"findings": []}, "malformed"), (gemini_samples.prompt_blocked(), "block")):
            with self.subTest(expected):
                self.forget_calls()
                self.screen({1: output})
                self.assertEqual(self.screen_calls()[0]["outcome"], expected)


class ReviewListTest(ScreenCase):
    def run_world(self, screen=None, mqm=None):
        code, out, err = self.run_cli("judge", transport=combined_transport(mqm=mqm, screen=screen))
        self.assertEqual(code, 0, err)

    def test_screening_results_are_must_see_and_show_the_spans_the_reason_and_whether_the_two_runs_agree(self):
        self.run_world(screen={1: {"items": [screen_item(), screen_item(verdict="Absent", type=None, source_span="預算超支", target_span="")]},
                               2: {"items": [screen_item(target_span="six months late")]}})
        must = self.review_part()
        self.assertIn("### 全文篩查的結果（2）", must)
        agreed = self.item(must, "Weakened（c）（兩次一致）")
        for shown in ("原文片段：「延誤半年」；譯文片段：「late」", "：虛構的理由", SOURCE_001, "9000001-001"):
            self.assertIn(shown, agreed["text"])
        self.assertIn("只有一次提出，或兩次沒有配對", self.item(must, "全文篩查：Absent")["text"])

    def agreement_notes(self) -> list[str]:
        must = self.review_part()
        return [line for line in must.splitlines() if line.startswith("- 全文篩查：")]

    def test_two_items_are_the_same_place_only_when_the_verdict_matches_and_the_spans_overlap(self):
        cases = {
            "same verdict, overlapping target spans": ([screen_item(target_span="six months late")], [screen_item(target_span="late and over")], ["兩次一致"]),
            "different verdict, same spans": ([screen_item()], [screen_item(verdict="Amplification", type=None)], ["只有一次", "只有一次"]),
            "same verdict, spans far apart": ([screen_item(target_span="renovation")], [screen_item(target_span="budget")], ["只有一次", "只有一次"]),
            "absent items overlap in the source": ([screen_item(verdict="Absent", type=None, source_span="預算超支", target_span="")],
                                                   [screen_item(verdict="Absent", type=None, source_span="預算", target_span="")], ["兩次一致"]),
            "absent against weakened": ([screen_item(verdict="Absent", type=None, source_span="延誤半年", target_span="")], [screen_item()], ["只有一次", "只有一次"]),
        }
        for name, (first, second, expected) in cases.items():
            with self.subTest(name):
                self.forget_calls()
                self.run_world(screen={1: {"items": first}, 2: {"items": second}})
                notes = self.agreement_notes()
                self.assertEqual(len(notes), len(expected), notes)
                for note, keyword in zip(notes, expected):
                    self.assertIn(keyword, note)

    def test_both_calls_failing_is_an_unjudged_item_and_one_success_makes_its_items_uncertain(self):
        self.run_world(screen={1: gemini_samples.prompt_blocked(), 2: gemini_samples.prompt_blocked()})
        self.assertIn("全文篩查的兩次請求都失敗：第 1 次 block", self.review_part())
        self.forget_calls()
        self.run_world(screen={1: {"items": [screen_item()]}, 2: gemini_samples.prompt_blocked()})
        self.assertIn("只有一次提出，或兩次沒有配對", self.review_part())

    def test_invalid_screening_items_are_listed_as_invalid_judge_output(self):
        self.run_world(screen={1: {"items": [screen_item(source_span="不存在的片段")]}})
        must = self.review_part()
        self.assertIn("全文篩查第 1 次的條目無效：原文片段不是任何一個 Component 原文的逐字子字串", must)
        self.assertIn("### 無效的 Judge 輸出", must)

    def test_fidelity_of_force_findings_from_mqm_join_the_same_must_see_group_even_when_consistent(self):
        fof = {"findings": [finding(dimension="Fidelity-of-force", subtype="softening (a)", severity="major")], "source_defects": []}
        self.run_world(mqm={"9000001-001": fof})   # 兩次都提出：一致
        must = self.review_part()
        self.assertIn("### Fidelity-of-force 的 Finding（MQM）（1）", must)
        self.assertIn("（兩次一致）", self.item(must, "Fidelity-of-force／softening (a)／major")["text"])

    def test_screening_items_can_be_accepted_or_rejected_but_not_overridden(self):
        self.run_world(screen={1: {"items": [screen_item()]}, 2: {"items": [screen_item()]}})
        must = self.review_part()
        item = self.item(must, "全文篩查：Weakened")
        self.decide(item, "accept", note="確認要跟進")
        self.assertEqual(self.run_cli("report")[0], 0)
        os.remove(os.path.join(self.out_dir, "review-decisions.json"))
        self.decide(item, "override", severity="minor")
        code, out, err = self.run_cli("report")
        self.assertNotEqual(code, 0)
        self.assertIn("只能 accept 或 reject", err)
        self.assertRegex(item["key"], r"^screening-[0-9a-f]{16}$")


class NoEffectOnResultsTest(ScreenCase):
    def sections(self, report: str):
        gate = report.split("### a × trad-to-en ・ 分組 general")[1].split("\n##", 1)[0]
        judge = report.split("### a × trad-to-en × Judge j1")[1].split("\n## ", 1)[0]
        return gate, judge

    def test_screening_results_change_neither_the_gate_nor_the_scores(self):
        fact = {"9000001-001": lambda p: fact_output(p, {S1_FACT: WEAK})}
        mqm = {("9000001-001", 1): {"findings": [finding()], "source_defects": []}}
        self.use_judging(screening=False)   # 先只有 MQM 與 fact 的結果；篩查之後才加
        self.run_judges(mqm=mqm, fact=fact)
        before = self.sections(self.report_text())
        # 篩查的結果很多：每次都列出好幾處
        busy = {"items": [screen_item(), screen_item(verdict="Absent", type=None, source_span="預算超支", target_span=""),
                          screen_item(verdict="Amplification", type=None, source_span="十分不滿", target_span="deeply")]}
        self.assertEqual(self.run_cli("judge", "--task", "screen", transport=screen_transport({1: busy, 2: busy}))[0], 0)
        after = self.sections(self.report_text())
        self.assertEqual(before, after)
        self.assertIn("### 全文篩查的結果（3）", self.part(self.review_text(), "必看"))
