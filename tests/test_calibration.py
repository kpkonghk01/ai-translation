"""Gold set 與校準：格式與驗證、gold sample、calibrate 的請求與續跑、各項指標（手算）、分歧清單、報告裡的 recall。從 main(argv) 進入，
transport 注入假的。只用虛構資料。指標的預期數字見 calibration_helpers 的說明，都是手算的。"""
import json
import os
import shutil
import tempfile
from unittest import mock

import gemini_samples
import openrouter_samples
from calibration_helpers import FACT_BUDGET, FACT_DELAY, GOLD, J2, SOURCE_001, CalibrationCase, gold_item, j1_transport, mqm, routed
from fake_transport import FakeTransport, FunctionTransport
from fixtures import FAKE_KEY
from judge_helpers import NO_PROBLEMS, fact_output, finding, system_text, user_payload
from translation_eval import prompts


class GoldSetTest(CalibrationCase):
    def assert_rejected(self, needle):
        for command in (lambda: self.run_gold("check"), lambda: self.calibrate("--judge", "j1", transport=FakeTransport())):
            code, out, err = command()
            self.assertNotEqual(code, 0)
            self.assertIn(needle, err)
            for text in (SOURCE_001, "虛構"):
                self.assertNotIn(text, err + out)
        for name in ("gold-calls.jsonl", "calibration.md", "calibration-disagreements.md", "gold-sample.md"):
            self.assertFalse(os.path.exists(os.path.join(self.out_dir, name)), name)   # 先驗證，後寫入

    def test_check_lists_the_counts_by_category_direction_and_domain_and_nothing_else(self):
        code, out, err = self.run_gold("check")
        self.assertEqual(code, 0, err)
        self.assertIn("Gold set：6 條", out)
        self.assertIn("- 類別：annotated 2、variant 2、clean 2（目標約 50／25／25", out)
        self.assertIn("- Direction：trad-to-en 3、trad-to-simplified 3", out)
        self.assertIn("- domain：china-political 2、general 4", out)
        self.assertIn("variant/trad-to-simplified/china-political 1", out)
        self.assertNotIn(SOURCE_001, out)

    def test_a_span_that_is_not_a_verbatim_substring_is_rejected(self):
        self.edited_gold(lambda items: items[0]["findings"][0].update(target_span="way over budget"))
        self.assert_rejected("Gold set 項目 g01：預期的 Finding 1：譯文片段不是逐字子字串")
        self.edited_gold(lambda items: items[0]["findings"][0].update(source_span="不存在的片段"))
        self.assert_rejected("原文片段不是逐字子字串")
        self.edited_gold(lambda items: items[0]["findings"][0].update(source_span="", target_span=""))
        self.assert_rejected("兩個片段都是空的")

    def test_spans_are_compared_after_squashing_whitespace_like_the_judges_output(self):
        self.edited_gold(lambda items: items[0]["findings"][0].update(target_span="over   budget"))
        self.assertEqual(self.run_gold("check")[0], 0)

    def test_a_fact_that_ground_truth_does_not_have_or_that_points_nowhere_is_rejected(self):
        self.edited_gold(lambda items: items[1]["facts"][0].update(fact="ground truth 沒有的事實"))
        self.assert_rejected("Gold set 項目 g02：預期的事實 1：不是 ground truth 這個 Component 的事實")
        self.edited_gold(lambda items: items[1].update(ground_truth={"article_id": "9000001", "component_id": "9000001-099"}))
        self.assert_rejected("沒有 Sensitive sentence")
        self.edited_gold(lambda items: items[1]["facts"].append(dict(items[1]["facts"][0])))
        self.assert_rejected("預期的事實有重覆")
        self.edited_gold(lambda items: items[1].update(source="翻新工程延誤半年，預算超支。另外多了一句。"))
        self.assert_rejected("原文與 ground truth 指向的 Component 不同")

    def test_the_domain_has_to_be_the_domain_of_the_ground_truth_article_the_item_points_at(self):
        self.edited_gold(lambda items: items[1].update(domain="china-political"))   # g02 指向 9000001（general）
        self.assert_rejected("domain 是 china-political，但 ground truth 的文章 9000001 是 general")
        self.edited_gold(lambda items: items[3].update(domain="general"))   # g04 指向 9000002（china-political）
        self.assert_rejected("domain 是 general，但 ground truth 的文章 9000002 是 china-political")

    def test_the_category_rules_are_enforced(self):
        cases = [(lambda items: items[2].update(findings=[{"dimension": "Fluency", "severity": "minor", "target_span": "late"}]), "clean 沒有預期的 Finding"),
                 (lambda items: items[2]["facts"][0].update(verdict="Weakened"), "clean 沒有預期的 Finding，事實都是 Present"),
                 (lambda items: items[1].update(facts=[{"fact": FACT_DELAY, "verdict": "Present"}]), "variant 要有至少一條預期是 Weakened 或 Absent 的事實"),
                 (lambda items: items[0].update(findings=[]), "annotated 要有預期的 Finding 或事實"),
                 (lambda items: items[1].pop("ground_truth"), "facts 要有 ground_truth"),
                 (lambda items: items[1].update(facts=[]), "ground_truth 要和 facts 一起寫"),
                 (lambda items: items[1]["facts"][0].update(weakening_type="amplification"), "Weakened／Absent 的類型是 a–f"),
                 (lambda items: items[2]["facts"][0].update(weakening_type="a"), "Present 只能標 amplification"),
                 (lambda items: items[0].update(category="mystery"), "items.0.category")]
        for mutate, needle in cases:
            with self.subTest(needle):
                self.edited_gold(mutate)
                self.assert_rejected(needle)

    def test_duplicate_ids_a_missing_calibration_setting_and_facts_without_ground_truth_are_rejected(self):
        self.edited_gold(lambda items: items.append(dict(items[0])))
        self.assert_rejected("Gold set：項目 id 重覆：g01")
        self.write_gold(GOLD)
        self.extra_experiment.pop("ground_truth")
        self.write_json(self.config_path, self.experiment())
        self.assert_rejected("facts 要實驗設定有 ground_truth")
        self.extra_experiment.pop("calibration")
        self.write_json(self.config_path, self.experiment())
        code, out, err = self.run_gold("check")
        self.assertNotEqual(code, 0)
        self.assertIn("沒有 calibration", err)


class SampleTest(CalibrationCase):
    def sampled_ids(self, *extra):
        code, out, err = self.run_gold("sample", *extra)
        self.assertEqual(code, 0, err)
        text = self.output_text("gold-sample.md")
        return [line.split("`")[1] for line in text.splitlines() if line.startswith("## `")], text, out

    def test_the_sample_is_fixed_by_the_seed_and_has_the_texts_and_the_expected_answers_for_the_reviewer(self):
        ids, text, out = self.sampled_ids("--count", "3", "--seed", "7")
        self.assertEqual(len(ids), 3)
        self.assertEqual(ids, self.sampled_ids("--count", "3", "--seed", "7")[0])   # 同一個種子：相同
        self.assertIn("3／6 條（種子 7）", out)
        self.assertNotIn(SOURCE_001, out)   # 終端只印數量
        for item in (i for i in GOLD["items"] if i["id"] in ids):
            self.assertIn(item["source"], text)
            self.assertIn(item["translation"], text)
        self.assertIn("預期的 Finding：Accuracy／major", self.sampled_ids("--count", "6")[1])
        self.assertIn("→ Weakened（c）", self.sampled_ids("--count", "6")[1])

    def test_the_seed_changes_the_sample_and_the_default_is_thirty_or_all_when_there_are_fewer(self):
        samples = {tuple(self.sampled_ids("--count", "2", "--seed", str(seed))[0]) for seed in range(1, 12)}
        self.assertGreater(len(samples), 1)
        self.assertEqual(len(self.sampled_ids()[0]), 6)   # 預設 30 條，Gold set 只有 6 條：全部

    def test_a_sampled_item_usually_stays_in_the_sample_when_other_items_are_added(self):
        before = set(self.sampled_ids("--count", "3", "--seed", "2")[0])
        self.edited_gold(lambda items: items.append(gold_item("g07", "clean", "trad-to-en", "general", SOURCE_001, "A clean translation.")))
        after = set(self.sampled_ids("--count", "3", "--seed", "2")[0])
        self.assertLessEqual(len(before - after), 1)   # 新的一條最多擠掉一條

    def test_an_invalid_gold_set_writes_no_sample(self):
        self.edited_gold(lambda items: items[0]["findings"][0].update(source_span="不存在的片段"))
        code, out, err = self.run_gold("sample")
        self.assertNotEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "gold-sample.md")))


class RunTest(CalibrationCase):
    def test_each_item_gets_two_mqm_requests_and_each_item_with_facts_two_more(self):
        transport = j1_transport()
        code, out, err = self.calibrate("--judge", "j1", transport=transport)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(transport.requests), 18)   # 6 條 × 2 + 3 條有事實的 × 2
        calls = self.gold_calls()
        self.assertEqual(sorted((c["item_id"], c["task"], c["call_index"]) for c in calls),
                         sorted([(i, "mqm", n) for i in ("g01", "g02", "g03", "g04", "g05", "g06") for n in (1, 2)]
                                + [(i, "fact", n) for i in ("g02", "g03", "g04") for n in (1, 2)]))
        self.assertNotIn("虛構", out)   # 終端只印數量
        self.assertIn("完成 18 個請求", out)

    def test_the_requests_have_the_same_format_as_the_real_judging_with_the_items_context_and_the_ground_truth_facts(self):
        transport = j1_transport()
        self.calibrate("--judge", "j1", transport=transport)
        payloads = [user_payload(r) for r in transport.requests]
        mqm_g06 = next(p for p in payloads if p["component"]["id"] == "g06" and "sentences" not in p)
        self.assertEqual(mqm_g06["direction"], "trad-to-simplified")
        self.assertEqual(mqm_g06["context_before"]["source"], "虛構的前文。")
        self.assertIsNone(mqm_g06["context_after"])
        self.assertEqual(mqm_g06["component"]["translation"], "翻新工程延误半年，预算超支。")
        fact_g02 = next(p for p in payloads if p["component"]["id"] == "g02" and "sentences" in p)
        self.assertEqual(fact_g02["sentences"][0]["anchor"], "預算超支")
        self.assertEqual([f["fact"] for f in fact_g02["sentences"][0]["facts"]], [FACT_DELAY, FACT_BUDGET])   # ground truth 這句的全部事實
        self.assertIn("Weakened", system_text(next(r for r in transport.requests if "sentences" in user_payload(r))))
        self.assertIn("MQM", system_text(transport.requests[0]))

    def test_a_rerun_skips_requests_that_have_a_current_record_and_sends_nothing_without_judge(self):
        self.calibrate_j1()
        transport = j1_transport()
        code, out, _ = self.calibrate("--judge", "j1", transport=transport)
        self.assertEqual((code, transport.requests), (0, []))
        self.assertIn("已有新的紀錄而略過：18", out)
        code, out, _ = self.calibrate(transport=FakeTransport())   # 沒有 --judge：只重算
        self.assertEqual(code, 0)
        self.assertNotIn("完成", out)

    def resent(self, *args) -> list[str]:
        transport = j1_transport()
        self.assertEqual(self.calibrate("--judge", "j1", *args, transport=transport)[0], 0)
        return sorted({user_payload(r)["component"]["id"] for r in transport.requests})

    def test_changing_what_is_sent_for_an_item_resends_only_that_items_requests_and_replaces_the_old_ones(self):
        self.calibrate_j1()
        self.edited_gold(lambda items: items[1].update(translation="The renovation ran slightly behind schedule."))
        transport = j1_transport()
        self.calibrate("--judge", "j1", transport=transport)
        self.assertEqual(sorted({user_payload(r)["component"]["id"] for r in transport.requests}), ["g02"])
        self.assertEqual(len(transport.requests), 4)   # g02：MQM 2、fact 2
        self.assertEqual(len(self.gold_calls()), 18)   # 舊的被換掉，不是疊上去

    def test_changing_the_ground_truth_sentence_an_item_points_at_makes_that_items_requests_stale_even_when_the_item_is_unchanged(self):
        self.calibrate_j1()
        self.edited_ground_truth(lambda a: a[1]["items"][0].update(anchor="不滿"))   # g04 指向的句子的 anchor 改了；事實與 Gold set 都沒變
        self.assertEqual(self.resent(), ["g04"])

    def test_changing_the_ground_truth_fact_text_resends_the_items_that_point_at_it(self):
        self.calibrate_j1()
        self.edited_ground_truth(lambda a: a[1]["items"][0]["must_retain"][0].update(fact="不滿的是市民本人"))
        self.edited_gold(lambda items: items[3]["facts"][0].update(fact="不滿的是市民本人"))
        self.assertEqual(self.resent(), ["g04"])

    def test_editing_only_the_expected_answers_or_notes_resends_nothing_because_they_are_not_sent_to_the_judge(self):
        self.calibrate_j1()
        self.edited_gold(lambda items: (items[0]["findings"][0].update(severity="minor", note="虛構的備註"), items[1]["facts"][0].update(weakening_type="d")))
        self.assertEqual(self.resent(), [])
        self.assertIn("預期 Finding 的 recall", self.output_text("calibration.md"))   # 指標用新的標註重算

    def test_a_changed_prompt_makes_only_that_tasks_requests_stale(self):
        self.calibrate_j1()
        with tempfile.TemporaryDirectory() as templates:
            shutil.copytree(prompts._DIR, os.path.join(templates, "t"))
            with open(os.path.join(templates, "t", "judge-facts.txt"), "a", encoding="utf-8") as f:
                f.write("\nAn extra sentence in the fact prompt.\n")
            with mock.patch.object(prompts, "_DIR", prompts.Path(templates) / "t"):
                transport = j1_transport()
                self.calibrate("--judge", "j1", transport=transport)
        self.assertEqual(len(transport.requests), 6)   # 只有 fact 的 6 個請求
        self.assertTrue(all("sentences" in user_payload(r) for r in transport.requests))

    def test_changing_the_judge_configuration_makes_every_record_stale(self):
        self.calibrate_j1()
        self.use_judging(judges=({**J2, "id": "j1", "role": "primary", "family": "claude", "model": "gemini-3.1-pro", "timeout_seconds": 31}, J2))
        self.extra_experiment["calibration"] = {"gold_set": self.gold_path}
        self.write_json(self.config_path, self.experiment())
        transport = j1_transport()
        self.calibrate("--judge", "j1", transport=transport)
        self.assertEqual(len(transport.requests), 18)

    def test_an_error_is_redone_only_with_redo_errors(self):
        transport = routed({"gemini-3.1-pro": ({("g01", 1): gemini_samples.client_error()}, {})})
        self.calibrate("--judge", "j1", transport=transport)
        self.assertEqual([c["outcome"] for c in self.gold_calls() if (c["item_id"], c["task"], c["call_index"]) == ("g01", "mqm", 1)], ["error"])
        again = routed({"gemini-3.1-pro": ({}, {})})
        self.calibrate("--judge", "j1", transport=again)
        self.assertEqual(again.requests, [])
        self.calibrate("--judge", "j1", "--redo-errors", transport=again)
        self.assertEqual(len(again.requests), 1)
        self.assertEqual([c["outcome"] for c in self.gold_calls() if (c["item_id"], c["task"], c["call_index"]) == ("g01", "mqm", 1)], ["ok"])

    def test_the_dry_run_sends_nothing_writes_nothing_and_needs_no_key(self):
        del os.environ["GEMINI_API_KEY"]
        code, out, err = self.calibrate("--judge", "j1", "--dry-run", transport=FakeTransport())
        self.assertEqual(code, 0, err)
        self.assertIn("請求數：18", out)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "gold-calls.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "calibration.md")))

    def test_a_missing_key_or_an_unknown_judge_stops_before_any_request(self):
        del os.environ["GEMINI_API_KEY"]
        code, out, err = self.calibrate("--judge", "j1", transport=FakeTransport())
        self.assertNotEqual(code, 0)
        self.assertIn("缺少環境變數 GEMINI_API_KEY", err)
        os.environ["GEMINI_API_KEY"] = "-".join(["fakekey", "unit", "test", "0001"])
        code, out, err = self.calibrate("--judge", "j9", transport=FakeTransport())
        self.assertNotEqual(code, 0)
        self.assertIn("--judge 不在實驗設定的 judging 裡：j9", err)

    def test_the_limit_stops_after_that_many_requests_and_a_pair_of_calls_can_be_split(self):
        transport = j1_transport()
        self.calibrate("--judge", "j1", "--limit", "3", transport=transport)
        self.assertEqual(len(transport.requests), 3)
        self.calibrate_j1()
        self.assertEqual(len(self.gold_calls()), 18)


class RunSafetyTest(CalibrationCase):
    def test_a_response_from_a_provider_that_is_not_allowed_stops_the_run_after_recording_it(self):
        env = mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": FAKE_KEY})
        env.start()
        self.addCleanup(env.stop)
        judge = {"id": "j1", "role": "primary", "family": "claude", "provider": "openrouter", "model": "anthropic/fake-judge", "api_key_env": "OPENROUTER_API_KEY",
                 "retry": {"max_attempts": 1, "base_delay_seconds": 0}, "routing": {"only": ["deepinfra"], "confirmed_on": "2026-11-09"}}
        self.use_judging(judges=(judge,))
        self.extra_experiment["calibration"] = {"gold_set": self.gold_path}
        self.write_json(self.config_path, self.experiment())
        transport = FunctionTransport(lambda r: openrouter_samples.ok(json.dumps(NO_PROBLEMS), "SomewhereElse"))
        code, _, err = self.calibrate("--judge", "j1", transport=transport)
        self.assertNotEqual(code, 0)
        self.assertEqual(len(transport.requests), 1)   # 資料已經送過去了：不再送下一個
        self.assertIn("provider", err)
        [call] = self.gold_calls()
        self.assertEqual((call["outcome"], call["reason"], call["served_provider"]), ("error", "provider 不在允許清單內", "SomewhereElse"))

    def test_a_key_that_comes_back_in_a_response_is_scrubbed_from_every_calibration_output(self):
        leaking = finding(severity="major", source_span="預算超支", target_span="over budget", reason=f"洩漏的 {FAKE_KEY}")
        leaking_response = gemini_samples.with_fields(gemini_samples.ok(json.dumps({"findings": [leaking], "source_defects": []})), modelVersion=FAKE_KEY)
        transport = FunctionTransport(lambda r: leaking_response if "sentences" not in user_payload(r) else gemini_samples.ok(json.dumps({"verdicts": []})))
        self.assertEqual(self.calibrate("--judge", "j1", transport=transport)[0], 0)
        self.assertIn("[REDACTED]", self.all_output_text())   # 對照：有東西被遮蓋了
        self.assertNotIn(FAKE_KEY, self.all_output_text())   # data/ 底下所有檔案：gold-calls.jsonl、gold-raw、calibration*.md

    def test_the_dry_run_estimates_the_input_size(self):
        del os.environ["GEMINI_API_KEY"]
        code, out, _ = self.calibrate("--judge", "j1", "--dry-run", transport=FakeTransport())
        self.assertEqual(code, 0)
        self.assertRegex(out, r"Judge j1：估計輸入字數約 \d+")

    def test_a_missing_key_leaves_the_outputs_untouched(self):
        del os.environ["GEMINI_API_KEY"]
        self.calibrate("--judge", "j1", transport=FakeTransport())
        for name in ("gold-calls.jsonl", "calibration.md", "calibration-disagreements.md"):
            self.assertFalse(os.path.exists(os.path.join(self.out_dir, name)), name)


class MetricsTest(CalibrationCase):
    def setUp(self):
        super().setUp()
        self.calibrate_j1()

    def lines(self, heading: str) -> str:
        return self.group(self.output_text("calibration.md"), heading)   # 每次重讀：有的測試會重跑校準

    def test_overall_metrics_are_the_hand_computed_numbers(self):
        overall = self.lines("全部")
        for expected in (
                "- 項目：6 條；MQM 評完 6、Unjudged 0、尚未評完 0；fact 評完 3、Unjudged 0、尚未評完 0",
                "- Unjudged 率（跑完的項目之中）：MQM 0/6（0.0%）；fact 0/3（0.0%）",
                "- 預期 Finding 的 recall：只計一致的 2/2（100.0%）；連 uncertain 一起計 2/2（100.0%）（Unjudged 的項目算沒有抓到）；對上的之中 Severity 一致 1/2（50.0%）",
                "- 驗證後 precision（annotated 項目）：2/3（66.7%）",
                "- clean 項目上的誤報：有 Finding 的 clean 項目，只計一致的 0/2（0.0%）；連 uncertain 一起計 1/2（50.0%）",
                "- 無效 Finding 率：1/9（11.1%）",
                "- 兩次一致率：Finding 3/5（60.0%）；事實 verdict 3/4（75.0%）",
                "- Finding 的錯誤率（annotated 與 clean 項目）：一致的 1/3（33.3%）；uncertain 的 1/1（100.0%）",
                "- 事實 verdict：有最終 verdict 3/4（75.0%）（uncertain 1、無效 0）；準確率（有最終 verdict 的）3/3（100.0%）",
                "- Weakened 的 recall：1/2（50.0%）；Absent 的 recall：1/1（100.0%）；失敗的事實（Weakened 或 Absent）的 recall：2/3（66.7%）"
                "（uncertain 與 Unjudged 算沒有抓到）；預期 Present 被判成失敗：0/1（0.0%）"):
            self.assertIn(expected, overall)

    def test_metrics_are_listed_apart_for_each_direction(self):
        english, simplified = self.lines("Direction trad-to-en"), self.lines("Direction trad-to-simplified")
        self.assertIn("- 預期 Finding 的 recall：只計一致的 1/1（100.0%）；連 uncertain 一起計 1/1（100.0%）（Unjudged 的項目算沒有抓到）；對上的之中 Severity 一致 1/1（100.0%）", english)
        self.assertIn("- 無效 Finding 率：1/4（25.0%）", english)
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：2/2（100.0%）", english)
        self.assertIn("預期 Present 被判成失敗：0/1（0.0%）", english)
        self.assertIn("對上的之中 Severity 一致 0/1（0.0%）", simplified)
        self.assertIn("- 驗證後 precision（annotated 項目）：1/2（50.0%）", simplified)
        self.assertIn("- 事實 verdict：有最終 verdict 0/1（0.0%）（uncertain 1、無效 0）；準確率（有最終 verdict 的）0/0（—）", simplified)
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：0/1（0.0%）", simplified)
        self.assertIn("預期 Present 被判成失敗：0/0（—）", simplified)

    def test_metrics_are_listed_apart_for_each_domain(self):
        political, general = self.lines("domain china-political"), self.lines("domain general")
        self.assertIn("- 項目：2 條；MQM 評完 2", political)
        self.assertIn("- 預期 Finding 的 recall：只計一致的 0/0（—）", political)
        self.assertIn("- clean 項目上的誤報：有 Finding 的 clean 項目，只計一致的 0/1（0.0%）；連 uncertain 一起計 1/1（100.0%）", political)
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：0/1（0.0%）", political)
        self.assertIn("對上的之中 Severity 一致 1/2（50.0%）", general)
        self.assertIn("- Finding 的錯誤率（annotated 與 clean 項目）：一致的 1/3（33.3%）；uncertain 的 0/0（—）", general)
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：2/2（100.0%）", general)

    def test_the_cells_the_gate_reads_and_the_variant_slices_for_the_q31_comparison_are_listed(self):
        text = self.output_text("calibration.md")
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：2/2（100.0%）", self.group(text, "Direction trad-to-en × domain general"))   # Gate 的 trad-to-en × general
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：0/1（0.0%）", self.group(text, "Direction trad-to-simplified × domain china-political"))
        self.assertNotIn("Direction trad-to-en × domain china-political", text)   # Gold set 沒有這一格：不列空的
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：2/2（100.0%）", self.group(text, "variant × domain general"))
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：0/1（0.0%）", self.group(text, "variant × domain china-political"))
        self.assertIn("- 項目：1 條", self.group(text, "variant × domain china-political"))   # 只有 variant，不含同一個 domain 的 clean

    def test_the_calibration_file_says_no_pass_mark_is_set_and_has_no_pass_or_fail_verdict(self):
        text = self.output_text("calibration.md")
        self.assertIn("合格線（recall、precision）尚未訂", text)
        for word in ("通過", "不合格", "合格：", "PASS", "FAIL"):
            self.assertNotIn(word, text.replace("合格線（recall、precision）尚未訂", ""))

    def test_a_blocked_judge_is_unjudged_and_its_expected_findings_stay_in_the_denominator_as_misses(self):
        self.forget_gold_calls()
        transport = routed({"gemini-3.1-pro": ({"g01": gemini_samples.prompt_blocked()}, {})})
        self.calibrate("--judge", "j1", transport=transport)
        overall = self.lines("全部")
        self.assertIn("MQM 評完 5、Unjudged 1、尚未評完 0", overall)
        self.assertIn("MQM 1/6（16.7%）", overall)   # Unjudged 率
        self.assertIn("預期 Finding 的 recall：只計一致的 0/2（0.0%）", overall)   # g01 的預期 Finding 不能因為 Judge 被擋就離開分母

    def test_items_that_were_not_run_yet_are_incomplete_not_unjudged_and_stay_out_of_the_denominators(self):
        self.forget_gold_calls()
        self.calibrate("--judge", "j1", "--limit", "2", transport=j1_transport())
        overall = self.lines("全部")
        self.assertIn("MQM 評完 1、Unjudged 0、尚未評完 5", overall)
        self.assertIn("預期 Finding 的 recall：只計一致的 1/1（100.0%）", overall)   # 只有 g01 跑完

    def test_an_item_that_changed_after_the_run_is_ignored_and_counted_as_stale(self):
        self.edited_gold(lambda items: items[0].update(translation="The renovation is six months late and well over budget."))
        self.calibrate()   # 只重算
        text = self.output_text("calibration.md")
        self.assertIn("過期而忽略 2", self.group(text, "Judge j1（primary）", "##"))
        self.assertIn("MQM 評完 5、Unjudged 0、尚未評完 1", self.group(text, "全部"))


class OtherJudgeOutcomesTest(CalibrationCase):
    def overall(self, judge_id: str) -> str:
        judge_part = self.output_text("calibration.md").split(f"## Judge {judge_id}（")[1].split("\n## ", 1)[0]
        return judge_part.split("### 全部\n", 1)[1].split("\n### ", 1)[0]

    def test_an_expected_finding_only_an_uncertain_finding_catches_counts_only_in_the_second_recall(self):
        accuracy = finding(severity="major", source_span="預算超支", target_span="over budget")
        self.calibrate("--judge", "j1", transport=routed({"gemini-3.1-pro": (mqm({("g01", 1): [accuracy]}), {})}))   # 只有第 1 次提出
        overall = self.overall("j1")
        self.assertIn("只計一致的 0/2（0.0%）；連 uncertain 一起計 1/2（50.0%）", overall)
        self.assertIn("對上的之中 Severity 一致 1/1（100.0%）", overall)

    def test_a_judge_that_calls_everything_present_misses_every_expected_failure_and_is_wrong_on_those_facts(self):
        self.calibrate("--judge", "j2", transport=routed({"gemini-3.1-other": ({}, {})}))   # 預設：每條事實 Present
        overall = self.overall("j2")
        self.assertIn("準確率（有最終 verdict 的）1/4（25.0%）", overall)   # 只有 g03 的 Present 對
        self.assertIn("Weakened 的 recall：0/2（0.0%）；Absent 的 recall：0/1（0.0%）；失敗的事實（Weakened 或 Absent）的 recall：0/3（0.0%）"
                      "（uncertain 與 Unjudged 算沒有抓到）；預期 Present 被判成失敗：0/1（0.0%）", overall)

    def test_a_present_fact_the_judge_calls_failed_is_a_false_alarm(self):
        weakened = lambda p: fact_output(p, {FACT_BUDGET: {"verdict": "Weakened", "type": "c", "quote": "over budget"}})   # noqa: E731
        self.calibrate("--judge", "j2", transport=routed({"gemini-3.1-other": ({}, {("g03", 1): weakened, ("g03", 2): weakened})}))
        self.assertIn("預期 Present 被判成失敗：1/1（100.0%）", self.overall("j2"))

    def test_a_judge_that_is_blocked_on_a_variants_facts_misses_them_instead_of_leaving_the_denominator(self):
        blocked = gemini_samples.prompt_blocked()
        self.calibrate("--judge", "j2", transport=routed({"gemini-3.1-other": ({}, {("g02", 1): blocked, ("g02", 2): blocked})}))
        overall = self.overall("j2")
        self.assertIn("fact 評完 2、Unjudged 1、尚未評完 0", overall)
        self.assertIn("fact 1/3（33.3%）", overall)   # Unjudged 率
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：0/3（0.0%）", overall)   # g02 的兩條預期失敗的事實還在分母（算沒有抓到）

    def test_each_judges_numbers_use_only_its_own_requests(self):
        self.calibrate_j1()
        self.calibrate("--judge", "j2", transport=routed({"gemini-3.1-other": ({}, {})}))
        text = self.output_text("calibration.md")
        for judge in ("j1（primary）", "j2（experiment）"):
            self.assertIn("新的請求：MQM 12、fact 6；過期而忽略 0", text.split(f"## Judge {judge}")[1].split("\n## ", 1)[0])


class DisagreementTest(CalibrationCase):
    def setUp(self):
        super().setUp()
        self.calibrate_j1()
        # j2：g01 兩次都沒有抓到預期的 Finding；g03（clean）兩次都多報；g02 的「工程延誤半年」判 Weakened 但類型是 b（預期 c）；其餘事實 Present
        spurious = finding(dimension="Fluency", subtype="awkward", severity="minor", source_span="", target_span="six months late")
        weak_b = lambda p: fact_output(p, {FACT_DELAY: {"verdict": "Weakened", "type": "b", "quote": "a little behind"}})   # noqa: E731
        transport = routed({"gemini-3.1-other": (mqm({("g03", 1): [spurious], ("g03", 2): [spurious]}), {("g02", 1): weak_b, ("g02", 2): weak_b})})
        code, out, err = self.calibrate("--judge", "j2", transport=transport)
        self.assertEqual(code, 0, err)
        self.text = self.output_text("calibration-disagreements.md")

    def item(self, item_id: str) -> str:
        return self.text.split(f"## `{item_id}`")[1].split("\n## ", 1)[0]

    def test_a_judge_that_misses_an_expected_finding_or_reports_one_the_annotation_does_not_have_is_listed(self):
        g01, g03 = self.item("g01"), self.item("g03")
        self.assertIn("Judge j2 沒有抓到預期的 Finding：Accuracy／major：原文「預算超支」譯文「over budget」", g01)
        self.assertNotIn("Judge j1 沒有抓到", g01)
        self.assertIn("Judge j2 多報（標註沒有）：Fluency／minor：原文「」譯文「six months late」", g03)

    def test_a_matched_finding_with_a_different_severity_is_listed(self):
        self.assertIn("Judge j1：Severity 不同（預期 minor、Judge major）：Fluency／minor：原文「」譯文「延误半年」", self.item("g06"))

    def test_a_fact_verdict_or_type_that_differs_from_the_expectation_is_listed_for_that_judge(self):
        g02 = self.item("g02")
        self.assertIn(f"Judge j2 對事實「{FACT_BUDGET}」：Present（預期 Absent）", g02)
        self.assertIn(f"Judge j2 對事實「{FACT_DELAY}」：Weakened 的類型是 b（預期 c）", g02)
        self.assertNotIn("Judge j1 對事實", g02)   # j1 全部答對
        self.assertIn("Judge j1 對事實「不滿的是市民陳虛構本人」：uncertain（預期 Weakened）", self.item("g04"))

    def test_two_judges_that_differ_are_listed_against_each_other(self):
        g01, g02 = self.item("g01"), self.item("g02")
        self.assertIn("只有 Judge j1 報：Accuracy／major：原文「預算超支」譯文「over budget」", g01)
        self.assertIn(f"事實「{FACT_BUDGET}」：Judge j1 Absent、Judge j2 Present", g02)

    def test_the_list_has_the_texts_but_the_calibration_file_has_only_numbers(self):
        self.assertIn("翻新工程延誤半年", self.text)
        self.assertNotIn(SOURCE_001, self.output_text("calibration.md"))
        self.assertNotIn("The renovation", self.output_text("calibration.md"))

    def test_an_item_the_judges_and_the_annotation_all_agree_on_is_not_listed(self):
        self.edited_gold(lambda items: items.append(gold_item("g07", "clean", "trad-to-en", "general", SOURCE_001, "A plain, correct translation.")))
        for judge, model in (("j1", "gemini-3.1-pro"), ("j2", "gemini-3.1-other")):
            self.assertEqual(self.calibrate("--judge", judge, transport=routed({model: ({}, {})}))[0], 0)   # 沒有任何 Finding：與標註一致
        text = self.output_text("calibration-disagreements.md")
        self.assertNotIn("## `g07`", text)
        self.assertIn("## `g01`", text)   # 有分歧的項目還在

    def test_with_one_judge_only_the_annotation_comparison_is_listed(self):
        self.forget_gold_calls()
        self.calibrate_j1()
        text = self.output_text("calibration-disagreements.md")
        self.assertNotIn("只有 Judge", text)
        self.assertIn("Judge j1 多報（標註沒有）：Fluency／minor", text)   # g05 的誤報（uncertain 的也算）


class GateRecallTest(CalibrationCase):
    def setUp(self):
        super().setUp()
        self.translate_both()

    def gate(self, group: str = "general") -> str:
        report = self.report_text()
        return report.split(f"### a × trad-to-en ・ 分組 {group}")[1].split("\n##", 1)[0]

    def test_without_calibration_the_gate_says_the_recall_is_not_measured(self):
        self.assertIn("Judge recall 未量度（還沒有校準結果）", self.gate())

    def test_the_gate_lists_the_primary_judges_recall_for_the_direction_and_domain(self):
        self.calibrate_j1()
        self.assertIn("Judge recall 2/2（100.0%）（Gold set 上預期失敗的事實，Judge j1，trad-to-en × general；uncertain 算沒有抓到；合格線尚未訂）", self.gate())

    def test_a_cell_the_gold_set_has_no_expected_failures_for_is_not_measured_and_says_why(self):
        self.calibrate_j1()
        self.assertIn("Judge recall 未量度（Gold set 在這一格沒有預期失敗的事實：trad-to-en × china-political）", self.gate("china-political"))   # g04 是繁→簡

    def test_a_judge_that_was_blocked_on_the_variants_gets_a_recall_of_zero_not_an_empty_cell(self):
        blocked = gemini_samples.prompt_blocked()
        self.calibrate("--judge", "j1", transport=routed({"gemini-3.1-pro": ({}, {("g02", 1): blocked, ("g02", 2): blocked})}))
        gate = self.gate()
        self.assertIn("Judge recall 0/2（0.0%）", gate)
        self.assertIn("其中 1 條項目 Judge 被擋（Unjudged，算沒有抓到）", gate)

    def test_a_cell_whose_items_have_no_request_results_yet_says_so_instead_of_blaming_the_gold_set(self):
        self.calibrate("--judge", "j1", "--limit", "2", transport=j1_transport())   # 只有 g01 的 MQM；其餘（包括所有 fact 的請求）都還沒跑
        self.assertIn("Judge recall 未量度（這一格的 2 條項目還沒有請求結果：尚未跑，或請求已過期：trad-to-en × general）", self.gate())

    def test_an_uncertain_verdict_counts_as_missed_in_the_calibration_summary(self):
        self.calibrate_j1()
        summary = self.report_text().split("## Judge 校準摘要")[1].split("\n## ", 1)[0]
        self.assertIn("失敗的事實（Weakened 或 Absent）的 recall：2/3（66.7%）（uncertain 與 Unjudged 算沒有抓到）", summary)

    def test_a_calibration_made_with_older_item_content_does_not_give_a_recall(self):
        self.calibrate_j1()
        self.edited_gold(lambda items: [i.update(translation=i["translation"] + " Really.") for i in items])   # 每個項目送給 Judge 的內容都改了
        gate = self.gate()
        self.assertIn("Judge recall 未量度", gate)
        self.assertIn("校準的請求都已過期", gate)

    def test_a_changed_fact_prompt_leaves_the_cell_without_results_until_it_is_calibrated_again(self):
        self.calibrate_j1()
        with tempfile.TemporaryDirectory() as templates:
            shutil.copytree(prompts._DIR, os.path.join(templates, "t"))
            with open(os.path.join(templates, "t", "judge-facts.txt"), "a", encoding="utf-8") as f:
                f.write("\nAn extra sentence in the fact prompt.\n")
            with mock.patch.object(prompts, "_DIR", prompts.Path(templates) / "t"):
                gate = self.gate()
        self.assertIn("Judge recall 未量度（這一格的 2 條項目還沒有請求結果：尚未跑，或請求已過期", gate)

    def test_a_group_that_is_not_a_domain_uses_all_domains_of_the_direction(self):
        self.extra_experiment["ground_truth"]["group_overrides"] = {"9000001": "q34"}
        self.write_json(self.config_path, self.experiment())
        self.calibrate_j1()
        section = self.gate("q34")
        self.assertIn("Judge recall 2/2（100.0%）", section)
        self.assertIn("Judge j1，trad-to-en；", section)   # 不是 domain 的分組：不分 domain

    def test_the_report_has_a_calibration_summary_with_numbers_only_and_so_does_the_summary_version(self):
        self.calibrate_j1()
        self.report_text()
        for name in ("report.md", "summary.md"):
            text = self.output_text(name)
            self.assertIn("## Judge 校準摘要", text)
            self.assertIn("- Gold set：6 條（hash ", text)
            self.assertIn("- 驗證後 precision（annotated 項目）：2/3（66.7%）", text)
            self.assertNotIn(SOURCE_001, text)

    def test_an_invalid_gold_set_stops_the_report_and_writes_nothing(self):
        self.edited_gold(lambda items: items[0]["findings"][0].update(source_span="不存在的片段"))
        self.assertEqual(self.run_cli("check")[0], 0)
        code, out, err = self.run_cli("report")
        self.assertNotEqual(code, 0)
        self.assertIn("Gold set 項目 g01", err)
        for name in ("report.md", "summary.md"):
            self.assertFalse(os.path.exists(os.path.join(self.out_dir, name)), name)

    def test_a_report_without_calibration_settings_has_no_calibration_section(self):
        self.extra_experiment.pop("calibration")
        self.write_json(self.config_path, self.experiment())
        self.assertNotIn("## Judge 校準摘要", self.report_text())
