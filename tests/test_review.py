"""review：審閱清單（必看與抽查）、決定檔、決定套用到 Gate 與分數、過期與不合法的決定。從 main(argv) 進入，transport 注入假的。只用虛構資料。"""
import json
import os
import re
import shutil
from pathlib import Path

import gemini_samples
from fake_transport import FakeTransport
from fixtures import TERM_LIST, component, ok_response, translation_with
from gate_helpers import HASH, KEY, S1_FACT, S2_FACT, WEAK, ReviewCase, combined_transport, translation_without
from judge_helpers import fact_output, finding

class ListTest(ReviewCase):
    def test_every_must_see_kind_is_listed_and_the_file_is_in_the_data_directory(self):
        self.all_kinds()
        out, _ = self.review()
        text = self.review_text()
        must = self.part(text, "必看")
        for heading in ("Gate 失敗的事實", "uncertain 的事實", "uncertain 的 Finding", "無效的 Judge 輸出", "Unjudged 的 Judge 請求（MQM、全文篩查）"):
            self.assertIn(f"### {heading}", must)
        self.assertTrue(os.path.exists(os.path.join(self.out_dir, "review.md")))
        self.assertNotIn("虛構", out)   # 終端只印數量

    def test_an_item_shows_its_key_hash_source_translation_quote_and_reason(self):
        self.all_kinds()
        failed = self.item(self.review_text(), "事實（detail）：工程延誤半年")
        for shown in ("翻新工程延誤半年，預算超支。", "The renovation is six months late and over budget.", "事實（detail）：工程延誤半年",
                      "Weakened（e）；引用「late」；理由：虛構的理由：語氣被削弱", "9000001 a trad-to-en full r1", "Judge j1"):
            self.assertIn(shown, failed["text"])
        self.assertRegex(failed["key"], r"^fact-[0-9a-f]{16}$")

    def test_consistent_findings_passed_facts_and_deterministic_findings_are_not_must_see(self):
        self.all_kinds()
        text = self.review_text()
        self.assertNotIn("awkward", self.part(text, "必看"))   # 兩次一致的 Finding
        self.set_review(sample_rate=1.0)
        text = self.review_text()
        self.assertIn("awkward", self.part(text, "抽查"))
        self.assertIn("### 通過的事實", self.part(text, "抽查"))

    def test_warnings_and_suspected_refusals_are_must_see(self):
        shutil.rmtree(self.data_dir)
        refused = translation_with(lambda t: t["components"][1].update(text="I'm sorry, but I can't help with translating this paragraph."))
        refused["components"][0]["text"] = "翻新工程延誤半年，預算超支。"   # 與原文相同：另一個警示
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(refused, ensure_ascii=False))))
        self.assertEqual(self.run_cli("check")[0], 0)
        must = self.part(self.review_text(), "必看")
        self.assertIn("### 疑似 Refusal", must)
        self.assertIn("### Deterministic 警示", must)

    def test_unjudged_facts_are_listed_one_by_one_and_overriding_them_finishes_the_gate(self):
        self.run_judges(fact={"9000001-001": gemini_samples.prompt_blocked()})   # 句 1 的兩條事實：兩次都被擋
        text = self.review_text()
        must = self.part(text, "必看")
        self.assertIn("### Unjudged 的事實（2）", must)
        self.assertIn("Judge 第 1 次：請求失敗（block）", must)
        for fact_text in ("事實（detail）：工程延誤半年", "事實（act）：預算超支"):
            self.decide(self.item(text, fact_text), "override", verdict="Present")
        gate = self.report_text().split("### a × trad-to-en ・ 分組 general")[1].split("\n##", 1)[0]
        self.assertIn("狀態：完成", gate)

    def test_the_list_needs_fresh_check_results_like_the_report(self):
        shutil.rmtree(os.path.join(self.out_dir), ignore_errors=True)
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        out, err = self.review(expect=1)
        self.assertIn("ok 的 Run 還沒有 check 結果", err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "review.md")))


class SampleTest(ReviewCase):
    def pool(self, text):
        return re.findall(r"#### `([a-z-]+-[0-9a-f]{16})`", self.part(text, "抽查"))

    def test_a_rate_of_one_lists_every_non_must_see_item_and_zero_lists_none(self):
        self.all_kinds()
        self.set_review(sample_rate=0.0)
        self.assertEqual(self.pool(self.review_text()), [])
        self.set_review(sample_rate=1.0)
        everything = self.pool(self.review_text())
        self.assertGreaterEqual(len(everything), 2)
        self.assertEqual(len(set(everything)), len(everything))
        must = re.findall(r"#### `([a-z-]+-[0-9a-f]{16})`", self.part(self.review_text(), "必看"))
        self.assertFalse(set(must) & set(everything))   # 必看的不會又出現在抽查

    def test_the_same_input_gives_the_same_sample_and_the_seed_changes_it(self):
        self.all_kinds()
        self.set_review(sample_rate=0.5, seed=1)
        first, again = self.review_text(), self.review_text()
        self.assertEqual(first, again)
        samples = set()
        for seed in range(1, 9):
            self.set_review(sample_rate=0.5, seed=seed)
            samples.add(tuple(self.pool(self.review_text())))
        self.assertGreater(len(samples), 1)   # 種子不同，抽出的不全一樣

    def test_a_sampled_item_stays_sampled_when_more_results_come_in(self):
        self.assertEqual(self.run_cli("judge", "--task", "fact", transport=combined_transport(fact={"9000001-001": fact_output}))[0], 0)
        self.assertEqual(self.run_cli("check")[0], 0)
        for seed in range(1, 30):   # 找一個真的抽到項目的種子：空的集合對任何集合都是子集合，什麼也證明不了
            self.set_review(sample_rate=0.5, seed=seed)
            before = set(self.pool(self.review_text()))
            if before:
                break
        self.assertTrue(before)
        title_finding = {"findings": [finding(dimension="Fluency", subtype="awkward", severity="minor",
                                              source_span="翻新工程延誤", target_span="renovation delayed")], "source_defects": []}
        self.assertEqual(self.run_cli("judge", "--task", "mqm", transport=combined_transport(mqm={"title": title_finding}))[0], 0)   # 之後才有 MQM 的結果
        after = set(self.pool(self.review_text()))
        self.assertLess(before, after)   # 多了新的項目（一致的 Finding），原來抽到的都還在


class DecisionTest(ReviewCase):
    def pending_fact_world(self):
        """句 2 的事實兩次不一致（uncertain），其餘都通過：Gate 未完成。"""
        self.run_judges(fact={("9000001-002", 2): lambda p: fact_output(p, {S2_FACT: WEAK})})

    def gate(self) -> str:
        text = self.report_text()
        return text.split("### a × trad-to-en ・ 分組 general")[1].split("\n##", 1)[0]

    def test_overriding_the_only_uncertain_fact_finishes_the_gate_with_the_reviewers_verdict(self):
        self.pending_fact_world()
        self.assertIn("狀態：未完成", self.gate())
        uncertain = self.item(self.review_text(), "事實（attribution）")
        self.decide(uncertain, "override", verdict="Present", note="虛構的備註")
        gate = self.gate()
        self.assertIn("狀態：完成", gate)
        self.assertIn("淡化率：0/2（0.0%）", gate)

    def test_overriding_to_weakened_fails_the_sentence_with_the_type_the_reviewer_gave(self):
        self.pending_fact_world()
        self.decide(self.item(self.review_text(), "事實（attribution）"), "override", verdict="Weakened", weakening_type="a")
        gate = self.gate()
        self.assertIn("淡化率：1/2（50.0%）", gate)
        self.assertIn("失敗的事實按淡化類型：a 1、", gate)

    def test_accepting_a_gate_failure_keeps_it_and_the_list_shows_the_decision(self):
        self.run_judges(fact={"9000001-001": lambda p: fact_output(p, {S1_FACT: WEAK})})
        failed = self.item(self.review_text(), "事實（detail）：工程延誤半年")
        self.decide(failed, "accept", note="確認是淡化")
        self.assertIn("淡化率：1/2（50.0%）", self.gate())
        shown = self.item(self.review_text(), "事實（detail）：工程延誤半年")["text"]
        self.assertIn("決定：accept（虛構審閱者，2026-11-09）：確認是淡化", shown)

    def mqm_world(self):
        self.run_judges(mqm={("9000001-001", 1): {"findings": [finding(severity="major")], "source_defects": []}})   # 只有一次提出：uncertain

    def main_line(self) -> str:
        return next(line for line in self.report_text().splitlines() if line.startswith("- Main：總罰分"))

    def test_an_uncertain_finding_counts_in_the_consistent_score_once_accepted(self):
        self.mqm_world()
        self.assertIn("總罰分 0（只計一致的 Finding）／5（連 uncertain 一起計）", self.main_line())
        self.decide(self.item(self.review_text(), "Accuracy／mistranslation"), "accept")
        self.assertIn("總罰分 5（只計一致的 Finding）／5（連 uncertain 一起計）", self.main_line())

    def test_a_rejected_finding_is_dropped_from_both_scores_and_a_severity_override_changes_the_weight(self):
        self.mqm_world()
        item = self.item(self.review_text(), "Accuracy／mistranslation")
        self.decide(item, "override", severity="critical")
        self.assertIn("總罰分 25（只計一致的 Finding）／25（連 uncertain 一起計）", self.main_line())
        path = os.path.join(self.out_dir, "review-decisions.json")
        os.remove(path)
        self.decide(item, "reject")
        self.assertIn("總罰分 0（只計一致的 Finding）／0（連 uncertain 一起計）", self.main_line())

    def terminology_world(self):
        """譯文把「陳虛構」譯成 Term List 沒有的寫法：一個 major 的 Terminology Finding（Main，罰分 5）。"""
        shutil.rmtree(self.data_dir)
        self.use_term_list(TERM_LIST)
        wrong = translation_with(lambda t: component(t, "002").update(text="Resident Chen said the delay left him deeply dissatisfied."))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(wrong, ensure_ascii=False))))
        self.run_judges()
        self.set_review(sample_rate=1.0)   # Deterministic Finding 不是必看，抽查才會列出
        return self.item(self.review_text(), "Terminology／term-mismatch")

    def test_a_rejected_content_finding_no_longer_counts_and_a_severity_override_changes_its_weight(self):
        finding_item = self.terminology_world()
        self.assertIn("Deterministic Finding：Accuracy 0、Terminology 1", self.report_text())
        self.assertIn("總罰分 5（", self.main_line())
        self.decide(finding_item, "override", severity="critical")
        self.assertIn("總罰分 25（", self.main_line())
        os.remove(os.path.join(self.out_dir, "review-decisions.json"))
        self.decide(finding_item, "reject")
        self.assertIn("Deterministic Finding：Accuracy 0、Terminology 0", self.report_text())
        self.assertIn("總罰分 0（", self.main_line())

    def test_a_finding_the_program_proved_can_only_be_accepted_and_a_missing_component_stays_absent(self):
        shutil.rmtree(self.data_dir)
        self.run_cli("translate", transport=FakeTransport(translation_without("9000001-002")))   # 句 2 的 Component 漏譯
        self.run_judges()
        self.set_review(sample_rate=1.0)
        text = self.review_text()
        omission, absent = self.item(text, "omission"), self.item(text, "事實（attribution）")
        for item, action, fields in ((omission, "reject", {}), (omission, "override", {"severity": "minor"}),
                                     (absent, "override", {"verdict": "Present"})):
            path = os.path.join(self.out_dir, "review-decisions.json")
            if os.path.exists(path):
                os.remove(path)
            self.decide(item, action, **fields)
            code, out, err = self.run_cli("report")
            self.assertNotEqual(code, 0, action)
            self.assertIn("只能 accept", err)
        os.remove(path)
        self.decide(omission, "accept")
        self.decide(absent, "accept")
        self.assertEqual(self.run_cli("report")[0], 0)
        self.assertIn("漏段數：1", self.report_text())   # 看過了，不改變結果

    def test_the_original_judge_output_is_not_changed_by_decisions(self):
        self.pending_fact_world()
        path = os.path.join(self.out_dir, "judge-calls.jsonl")
        before = Path(path).read_bytes()
        self.decide(self.item(self.review_text(), "事實（attribution）"), "override", verdict="Present")
        self.report_text()
        self.assertEqual(Path(path).read_bytes(), before)

    def test_the_report_opens_with_how_many_must_see_items_have_a_decision(self):
        self.pending_fact_world()
        self.assertIn("必看 1 項，已有決定 0 項（未決定 1）", self.report_text())
        self.decide(self.item(self.review_text(), "事實（attribution）"), "override", verdict="Present")
        self.assertIn("必看 1 項，已有決定 1 項（未決定 0）", self.report_text())


class InvalidDecisionTest(ReviewCase):
    def setUp(self):
        super().setUp()
        self.run_judges(fact={("9000001-002", 2): lambda p: fact_output(p, {S2_FACT: WEAK}),
                              "9000001-001": lambda p: fact_output(p, {S1_FACT: WEAK})},
                        mqm={("9000001-001", 1): {"findings": [finding()], "source_defects": []}})
        self.text = self.review_text()
        self.report_text()
        self.report_before = Path(self.out_dir, "report.md").read_bytes()

    def assert_both_fail(self, needle):
        for command in ("review", "report"):
            code, out, err = self.run_cli(command)
            self.assertNotEqual(code, 0, command)
            self.assertIn(needle, err)
            for article_text in ("翻新", "renovation", "虛構"):
                self.assertNotIn(article_text, err + out)
        self.assertEqual(Path(self.out_dir, "report.md").read_bytes(), self.report_before)   # 失敗不改動既有的報告

    def test_a_decision_for_a_key_that_does_not_exist_fails_and_is_listed(self):
        self.decide({"key": "fact-0000000000000000", "hash": "0" * 16}, "accept")
        self.assert_both_fail("fact-0000000000000000：項目不存在")

    def test_a_decision_whose_input_changed_is_stale_and_fails(self):
        item = self.item(self.text, "事實（detail）：工程延誤半年")
        self.decide(item, "accept")
        self.forget_calls()   # 重新判過：同一條事實，但 Judge 這次的回答不同，輸入的 hash 就變了
        self.run_judges(fact={"9000001-001": lambda p: fact_output(p, {S1_FACT: WEAK | {"reason": "另一個理由"}})})
        self.assert_both_fail(f"{item['key']}：輸入的 hash 已經改變（過期）")

    def test_a_decision_on_a_warning_goes_stale_when_the_source_text_changes(self):
        shutil.rmtree(self.data_dir)
        refused = translation_with(lambda t: t["components"][1].update(text="I'm sorry, but I can't help with translating this paragraph."))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(refused, ensure_ascii=False))))
        self.run_judges()
        item = self.item(self.review_text(), "suspected-refusal")
        self.decide(item, "accept", note="確認是拒絕")
        self.assertEqual(self.run_cli("report")[0], 0)
        article_path = os.path.join(self.articles_dir, "9000001.json")
        article = json.loads(Path(article_path).read_text(encoding="utf-8"))
        article["components"][1]["text"] = "市民陳虛構表示，工程拖延令他十分不滿意。"   # 原文改了，警示的種類沒變
        self.write_json(article_path, article)
        self.assertEqual(self.run_cli("check")[0], 0)
        for command in ("review", "report"):
            code, out, err = self.run_cli(command)
            self.assertNotEqual(code, 0)
            self.assertIn(f"{item['key']}：輸入的 hash 已經改變（過期）", err)

    def test_a_decision_that_does_not_fit_its_item_fails_with_the_reason(self):
        fact_failed = self.item(self.text, "事實（detail）：工程延誤半年")
        fact_uncertain = self.item(self.text, "事實（attribution）")
        for action, item, needle in (("reject", fact_failed, "事實不能駁回"), ("accept", fact_uncertain, "沒有最終 verdict 可以接受")):
            with self.subTest(action):
                path = os.path.join(self.out_dir, "review-decisions.json")
                if os.path.exists(path):
                    os.remove(path)
                self.decide(item, action)
                self.assert_both_fail(needle)

    def test_two_decisions_for_one_item_and_a_decision_file_with_a_wrong_shape_fail_without_echoing_notes(self):
        item = self.item(self.text, "事實（detail）：工程延誤半年")
        self.decide(item, "accept")
        self.decide(item, "accept")
        self.assert_both_fail("不只一條決定")
        path = os.path.join(self.out_dir, "review-decisions.json")
        self.write_json(path, {"decisions": [{"key": item["key"], "input_hash": item["hash"], "action": "override", "reviewer": "虛構審閱者",
                                              "reviewed_on": "2026-11-09", "note": "虛構的備註不可以出現在錯誤訊息裡"}]})
        for command in ("review", "report"):
            code, out, err = self.run_cli(command)
            self.assertNotEqual(code, 0)
            self.assertIn("override", err)
            self.assertNotIn("虛構的備註", err)


class KeyAndCoverageTest(ReviewCase):
    def test_findings_with_the_same_identity_in_one_component_get_different_keys_and_decisions_apply_to_one(self):
        # 同一個 Finding 在第 1 次出現兩個、第 2 次一個：一個一致、一個 uncertain，兩個項目身分一樣
        twin = {"findings": [finding(severity="major"), finding(severity="major")], "source_defects": []}
        self.run_judges(mqm={("9000001-001", 1): twin, ("9000001-001", 2): {"findings": [finding(severity="major")], "source_defects": []}})
        self.set_review(sample_rate=1.0)
        text = self.review_text()
        chunks = [c for c in text.split("\n#### ")[1:] if "Accuracy／mistranslation" in c]
        keys = [KEY.search(c).group(1) for c in chunks]
        self.assertEqual((len(keys), len(set(keys))), (2, 2))
        consistent = next(c for c in chunks if "兩次一致" in c)
        self.decide({"key": KEY.search(consistent).group(1), "hash": HASH.search(consistent).group(1)}, "reject")
        line = next(l for l in self.report_text().splitlines() if l.startswith("- Main：總罰分"))
        self.assertIn("總罰分 0（只計一致的 Finding）／5（連 uncertain 一起計）", line)   # 只有被駁回的那一個不見了，uncertain 的還在

    def test_residual_text_found_twice_gives_two_check_items_with_different_keys(self):
        shutil.rmtree(self.data_dir)
        twice = translation_with(lambda t: component(t, "002").update(text="Resident Chan Fictional said 新聞 and 新聞 were wrong."))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(twice, ensure_ascii=False))))
        self.run_judges()
        self.set_review(sample_rate=1.0)
        chunks = [c for c in self.review_text().split("\n#### ")[1:] if "residual-han" in c]
        keys = [KEY.search(c).group(1) for c in chunks]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertGreaterEqual(len(keys), 1)

    def test_a_component_that_is_not_translated_is_shown_whole_and_a_decision_on_it_goes_stale_when_it_changes(self):
        shutil.rmtree(self.data_dir)
        edited = translation_with(lambda t: component(t, "004").update(code="<div>changed</div>"))   # code 不翻譯，被改了
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(edited, ensure_ascii=False))))
        self.run_judges()
        item = self.item(self.review_text(), "untranslatable-changed")
        self.assertIn("<div>embed</div>", item["text"])      # 原文
        self.assertIn("<div>changed</div>", item["text"])    # 譯文
        self.decide(item, "accept")
        self.assertEqual(self.run_cli("report")[0], 0)
        shutil.rmtree(self.data_dir)
        other = translation_with(lambda t: component(t, "004").update(code="<div>changed again</div>"))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(other, ensure_ascii=False))))
        self.run_judges()
        os.makedirs(self.out_dir, exist_ok=True)
        self.write_json(os.path.join(self.out_dir, "review-decisions.json"), {"decisions": [
            {"key": item["key"], "input_hash": item["hash"], "action": "accept", "reviewer": "虛構審閱者", "reviewed_on": "2026-11-09"}]})
        code, out, err = self.run_cli("report")
        self.assertNotEqual(code, 0)
        self.assertIn(f"{item['key']}：輸入的 hash 已經改變（過期）", err)

    def test_invalid_fact_output_that_belongs_to_no_fact_is_listed_as_invalid_judge_output(self):
        weird = {"verdicts": [{"fact_id": "F99", "verdict": "Present", "quote": "x", "reason": "x"}]}
        self.run_judges(fact={("9000001-001", 1): weird})
        must = self.part(self.review_text(), "必看")
        self.assertIn("### 無效的 Judge 輸出（1）", must)
        self.assertIn("不認識的 fact_id", must)

    def test_the_run_limit_leaves_out_the_mqm_items_of_runs_it_does_not_judge(self):
        shutil.rmtree(self.data_dir)
        self.runs = 2
        self.write_json(self.config_path, self.experiment())
        self.run_cli("translate", transport=FakeTransport(ok_response(), ok_response()))
        uncertain = {"findings": [finding()], "source_defects": []}   # 每個 Run 的第 1 次呼叫提出，第 2 次沒有：uncertain
        self.run_judges(mqm={("9000001-001", 1): uncertain, ("9000001-001", 3): uncertain})   # 請求的序號跨 Run 累計：第 2 個 Run 是第 3、4 次
        
        self.assertEqual(self.part(self.review_text(), "必看").count("Accuracy／mistranslation"), 2)
        self.use_judging(mqm_run_limit={"a": 1})   # 之後只評第 1 個 Run
        must = self.part(self.review_text(), "必看")
        self.assertEqual(must.count("Accuracy／mistranslation"), 1)
        self.assertIn("a trad-to-en full r1", must)
        self.assertNotIn("a trad-to-en full r2", must)

    def test_the_report_always_opens_with_the_review_progress_even_without_judges_or_ground_truth(self):
        self.extra_experiment.pop("ground_truth")
        self.extra_experiment.pop("judging")
        self.write_json(self.config_path, self.experiment())
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertIn("## 審閱", self.report_text())

    def test_a_decision_is_checked_even_when_no_run_has_a_translation(self):
        shutil.rmtree(self.data_dir)
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked()))
        self.decide({"key": "fact-0000000000000000", "hash": "0" * 16}, "accept")
        code, out, err = self.run_cli("report")
        self.assertNotEqual(code, 0)
        self.assertIn("fact-0000000000000000：項目不存在", err)
