"""review：審閱清單（必看與抽查）、決定檔、決定套用到 Gate 與分數、過期與不合法的決定。從 main(argv) 進入，transport 注入假的。只用虛構資料。"""
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import gemini_samples
from fake_transport import FakeTransport
from fixtures import ok_response, translation_with
from gate_helpers import FactCase, combined_transport, translation_without
from judge_helpers import fact_output, finding

S1_FACT, S2_FACT = "工程延誤半年", "不滿的是市民陳虛構本人"
WEAK = {"verdict": "Weakened", "type": "e", "quote": "late", "reason": "虛構的理由：語氣被削弱"}
KEY = re.compile(r"`([a-z-]+-[0-9a-f]{16})`")
HASH = re.compile(r"輸入 hash：`([0-9a-f]{16})`")


class ReviewCase(FactCase):
    def run_judges(self, mqm=None, fact=None):
        code, _, err = self.run_cli("judge", transport=combined_transport(mqm, fact))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("check")[0], 0)

    def review(self, expect=0):
        code, out, err = self.run_cli("review")
        self.assertEqual(code, expect, err)
        return out, err

    def review_text(self) -> str:
        self.review()
        with open(os.path.join(self.out_dir, "review.md"), encoding="utf-8") as f:
            return f.read()

    def report_text(self) -> str:
        code, _, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def part(text: str, heading: str) -> str:
        """## 必看 或 ## 抽查 底下的內容。"""
        return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]

    @staticmethod
    def item(text: str, needle: str) -> dict:
        """審閱清單裡含 needle 的那個項目：{key, hash, text}。"""
        chunks = [c for c in text.split("\n#### ")[1:] if needle in c]
        assert len(chunks) == 1, f"含 {needle!r} 的項目有 {len(chunks)} 個"
        return {"key": KEY.search(chunks[0]).group(1), "hash": HASH.search(chunks[0]).group(1), "text": chunks[0]}

    def decide(self, item: dict, action: str, **fields) -> None:
        path = os.path.join(self.out_dir, "review-decisions.json")
        decisions = json.loads(Path(path).read_text(encoding="utf-8"))["decisions"] if os.path.exists(path) else []
        decisions.append({"key": item["key"], "input_hash": item["hash"], "action": action, "reviewer": "Pan",
                          "reviewed_on": "2026-11-09", **fields})
        self.write_json(path, {"decisions": decisions})

    def set_review(self, **review) -> None:
        self.extra_experiment["review"] = review
        self.write_json(self.config_path, self.experiment())

    def all_kinds(self):
        """必看每一種都出現的世界：Gate 失敗、uncertain 的事實與 Finding、無效的輸出、MQM 的 Unjudged，另有一個一致的 Finding。"""
        self.run_judges(
            mqm={("9000001-001", 1): {"findings": [finding()], "source_defects": []},
                 "9000001-002": gemini_samples.prompt_blocked(),
                 ("9000001-003", 1): {"findings": [finding(source_span="不存在的片段")], "source_defects": []},
                 "title": {"findings": [finding(dimension="Fluency", subtype="awkward", severity="minor",
                                                source_span="翻新工程延誤", target_span="renovation delayed")], "source_defects": []}},
            fact={"9000001-001": lambda p: fact_output(p, {S1_FACT: WEAK}),
                  ("9000001-002", 2): lambda p: fact_output(p, {S2_FACT: WEAK})})


class ListTest(ReviewCase):
    def test_every_must_see_kind_is_listed_and_the_file_is_in_the_data_directory(self):
        self.all_kinds()
        out, _ = self.review()
        text = self.review_text()
        must = self.part(text, "必看")
        for heading in ("Gate 失敗的事實", "uncertain 的事實", "uncertain 的 Finding", "無效的 Judge 輸出", "Unjudged 的 Component（MQM）"):
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

    def test_unjudged_facts_are_listed_one_by_one_and_a_decision_can_resolve_them(self):
        self.run_judges(fact={"9000001-001": gemini_samples.prompt_blocked()})   # 句 1 的兩條事實：兩次都被擋
        must = self.part(self.review_text(), "必看")
        self.assertIn("### Unjudged 的事實（2）", must)
        self.assertIn("Judge 第 1 次：請求失敗（block）", must)

    def test_the_list_needs_fresh_check_results_like_the_report(self):
        shutil.rmtree(os.path.join(self.out_dir), ignore_errors=True)
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        out, err = self.review(expect=1)
        self.assertIn("check", err)
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
        self.run_judges(fact={"9000001-001": fact_output})
        self.set_review(sample_rate=0.5, seed=3)
        before = set(self.pool(self.review_text()))
        self.run_judges(mqm={"title": {"findings": [finding(dimension="Fluency", subtype="awkward", severity="minor",
                                                           source_span="翻新工程延誤", target_span="renovation delayed")], "source_defects": []}})
        self.assertLessEqual(before, set(self.pool(self.review_text())))


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
        self.assertIn("決定：accept（Pan，2026-11-09）：確認是淡化", shown)

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

    def test_a_rejected_deterministic_finding_no_longer_counts(self):
        shutil.rmtree(self.data_dir)
        self.run_cli("translate", transport=FakeTransport(translation_without("9000001-003")))   # 圖說漏譯：漏段 Finding
        self.run_judges()
        report = self.report_text()
        self.assertIn("漏段數：1", report)
        self.set_review(sample_rate=1.0)   # Deterministic Finding 不是必看，抽查才會列出
        omission = self.item(self.review_text(), "omission")
        self.decide(omission, "reject")
        report = self.report_text()
        self.assertIn("漏段數：0", report)
        self.assertIn("Deterministic Finding：Accuracy 0", report)

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
        finding_item = self.item(self.text, "Accuracy／mistranslation")
        cases = (("reject", fact_failed, {}, "事實不能駁回"), ("accept", fact_uncertain, {}, "沒有最終 verdict 可以接受"),
                 ("override", finding_item, {"severity": "minor"}, None))
        for action, item, fields, needle in cases[:2]:
            with self.subTest(action):
                path = os.path.join(self.out_dir, "review-decisions.json")
                if os.path.exists(path):
                    os.remove(path)
                self.decide(item, action, **fields)
                self.assert_both_fail(needle)

    def test_two_decisions_for_one_item_and_a_decision_file_with_a_wrong_shape_fail_without_echoing_notes(self):
        item = self.item(self.text, "事實（detail）：工程延誤半年")
        self.decide(item, "accept")
        self.decide(item, "accept")
        self.assert_both_fail("不只一條決定")
        path = os.path.join(self.out_dir, "review-decisions.json")
        self.write_json(path, {"decisions": [{"key": item["key"], "input_hash": item["hash"], "action": "override", "reviewer": "Pan",
                                              "reviewed_on": "2026-11-09", "note": "虛構的備註不可以出現在錯誤訊息裡"}]})
        for command in ("review", "report"):
            code, out, err = self.run_cli(command)
            self.assertNotEqual(code, 0)
            self.assertIn("override", err)
            self.assertNotIn("虛構的備註", err)
