"""showcase-text --judged-in：本機展示頁的「被寫輕的真實例子」一節。從 main(argv) 進入。

--judged-in：Judge 評核過的實驗設定（通常是抽樣實驗）；這裡的測試用同一個虛構實驗當 --config 和 --judged-in
（兩篇文章：9000001 一般題材，001 有兩條事實；9000002 中國政治題材，002 有一條事實）。
每個（方向、題材組）的小節裡，每種淡化類型取一個被判淡化的事實：原文、譯文、要保留的事實、Judge 的引用與理由、白話的類型。只用虛構資料。"""
import json
import os
import re
import shutil
import unittest

import gemini_samples
from calibration_helpers import FACT_BUDGET, FACT_DELAY, FACT_WHO, GOOD_EN, SOURCE_001, SOURCE_002, CalibrationCase, weak
from fake_transport import FakeTransport
from fixtures import main_only_translation, ok_response, simplified_response
from gate_helpers import translation_without
from judge_helpers import fact_output, fact_transport

NAME = "測試模型甲（虛構公司）"
LABEL_A, LABEL_B, LABEL_C, LABEL_D = "施害者或行為主體被拿掉", "用詞被改得委婉或中性", "數量被寫得模糊", "具體細節被刪掉或概括"
REASON = "虛構的理由：細節被概括"
TRANSLATION_002 = "Resident Chan Fictional said the delay left him deeply dissatisfied."


def fails(**by_fact) -> dict:
    """{事實文字: (類型, 引用)} → fact_output 要覆寫的欄位：每條都判 Weakened。"""
    return {fact: {"verdict": "Weakened", "type": kind, "quote": quote, "reason": REASON} for fact, (kind, quote) in by_fact.items()}


class JudgedCase(CalibrationCase):
    def setUp(self):
        super().setUp()
        with open(self.approach_path, encoding="utf-8") as f:
            self.write_json(self.approach_path, {**json.load(f), "display_name": NAME})
        self.translate_both()

    def judge_facts(self, outputs: dict) -> None:
        code, _, err = self.run_cli("judge", "--task", "fact", transport=fact_transport(outputs))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("check")[0], 0)

    def judge_001(self, changes: dict, second: dict | None = None) -> None:
        """001 的事實按 changes 判（第 2 次按 second，沒有就同一個）；002 都判 Present。"""
        first = lambda p: fact_output(p, changes)   # noqa: E731
        again = first if second is None else (lambda p: fact_output(p, second))
        self.judge_facts({("9000001-001", 1): first, ("9000001-001", 2): again})

    def text_page(self, *extra) -> str:
        code, out, err = self.run_cli("showcase-text", "--judged-in", self.config_path, *extra)
        self.assertEqual(code, 0, err)
        return self.output_text("showcase-text.html")

    def cards(self, html: str) -> list[str]:
        section = html.split("被寫輕的例子")[1].split("</section>")[0]
        return section.split('<li class="weakened">')[1:]


class CardTest(JudgedCase):
    def test_a_card_has_the_fact_the_source_the_translation_the_judges_quote_and_reason_and_the_plain_type(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        [card] = self.cards(self.text_page())
        for shown in (LABEL_D, "要保留的事實：" + FACT_DELAY, SOURCE_001, GOOD_EN, "late", REASON, NAME, "繁→英", "一般題材"):
            self.assertIn(shown, card)

    def test_the_card_names_only_the_fact_that_failed_not_the_other_fact_of_the_sentence(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        [card] = self.cards(self.text_page())
        self.assertNotIn("要保留的事實：" + FACT_BUDGET, card)

    def test_a_fact_the_judge_found_present_is_not_an_example(self):
        self.judge_001({})
        html = self.text_page()
        self.assertEqual(self.cards(html), [])
        self.assertIn("沒有被判淡化的事實", html)

    def test_the_fact_is_written_once_as_the_fact_to_keep_not_again_among_the_judges_lines(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        [card] = self.cards(self.text_page())
        self.assertEqual(card.count(FACT_DELAY), 2)   # 一次是「要保留的事實」，一次是原文裡的字：Judge 的行不重覆事實，也沒有「事實（detail）」的標記
        self.assertNotIn("（detail）", card)

    def test_the_heading_is_the_type_in_plain_language_not_a_code(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        [card] = self.cards(self.text_page())
        self.assertTrue(card.startswith(f"<h4>{LABEL_D}</h4>"), card[:80])


class OneCardPerTypeTest(JudgedCase):
    """一個小節（一個方向、一個題材組）裡，每種類型一張卡。"""

    def test_there_is_one_card_per_type_in_the_order_of_the_types(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late"), FACT_BUDGET: ("a", "over budget")}))
        cards = self.cards(self.text_page())
        self.assertEqual(len(cards), 2)
        self.assertIn(LABEL_A, cards[0])   # a 在 d 前面，雖然 d 的事實在前
        self.assertIn(LABEL_D, cards[1])

    def test_the_card_of_a_type_is_the_first_one_in_the_record_not_a_picked_one(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late"), FACT_BUDGET: ("d", "over budget")}))
        [card] = self.cards(self.text_page())
        self.assertIn("要保留的事實：" + FACT_DELAY, card)   # 001 的兩條事實都是 d：取紀錄裡的第一條

    def test_a_group_with_its_own_failure_is_shown_with_its_own_source_translation_and_group(self):
        self.judge_facts({("9000002-002", 1): lambda p: fact_output(p, fails(**{FACT_WHO: ("a", "Resident")})),
                          ("9000002-002", 2): lambda p: fact_output(p, fails(**{FACT_WHO: ("a", "Resident")}))})
        [card] = self.cards(self.text_page())
        for shown in (SOURCE_002, TRANSLATION_002, "中國政治題材", "要保留的事實：" + FACT_WHO):
            self.assertIn(shown, card)

    def test_a_fact_where_the_two_judge_requests_disagree_on_the_type_gets_its_own_card(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}), second=fails(**{FACT_DELAY: ("c", "late")}))
        [card] = self.cards(self.text_page())
        self.assertIn("沒有單一類型", card)
        self.assertNotIn(LABEL_D, card)
        self.assertNotIn(LABEL_C, card)

    def test_a_card_with_no_single_type_comes_after_the_typed_cards(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late"), FACT_BUDGET: ("a", "over budget")}),
                       second=fails(**{FACT_DELAY: ("c", "late"), FACT_BUDGET: ("a", "over budget")}))
        cards = self.cards(self.text_page())
        self.assertEqual(len(cards), 2)
        self.assertIn(LABEL_A, cards[0])
        self.assertIn("沒有單一類型", cards[1])


class SectionPerCellTest(JudgedCase):
    """每個（方向、題材組）一個小節：同一種類型在不同組各有一張。"""

    def judge_both_groups(self) -> None:
        both = lambda component, fact, quote: {(component, 1): lambda p: fact_output(p, fails(**{fact: ("d", quote)})),   # noqa: E731
                                                (component, 2): lambda p: fact_output(p, fails(**{fact: ("d", quote)}))}
        self.judge_facts({**both("9000001-001", FACT_DELAY, "late"), **both("9000002-002", FACT_WHO, "Resident")})

    def subheadings(self, html: str) -> list[str]:
        return re.findall(r"<h3>(.*?)</h3>", html.split("被寫輕的例子")[1].split("</section>")[0])

    def test_the_same_type_in_two_groups_has_a_card_in_each_with_a_subheading_for_the_group(self):
        self.judge_both_groups()
        html = self.text_page()
        self.assertEqual(self.subheadings(html), ["繁→英・一般題材", "繁→英・中國政治題材"])
        general, political = self.cards(html)
        self.assertIn(SOURCE_001, general)
        self.assertIn(SOURCE_002, political)
        self.assertIn(LABEL_D, general)
        self.assertIn(LABEL_D, political)

    def test_a_group_with_nothing_weakened_has_its_subheading_and_says_so(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))   # 中國政治題材的事實都 Present
        html = self.text_page()
        self.assertEqual(self.subheadings(html), ["繁→英・一般題材", "繁→英・中國政治題材"])
        political = html.split("<h3>繁→英・中國政治題材</h3>")[1].split("</section>")[0]
        self.assertIn("這一格沒有被判淡化的事實", political)
        self.assertEqual(len(self.cards(html)), 1)

    def test_a_group_without_a_failure_does_not_say_so_when_it_has_one(self):
        self.judge_both_groups()
        self.assertNotIn("這一格沒有被判淡化的事實", self.text_page())


class SectionPerDirectionTest(CalibrationCase):
    """兩個方向：每個方向各有自己的小節，用各自的譯文。"""
    directions = ["trad-to-en", "trad-to-simplified"]

    def setUp(self):
        super().setUp()
        with open(self.approach_path, encoding="utf-8") as f:
            self.write_json(self.approach_path, {**json.load(f), "display_name": NAME})
        responses = [r for i in self.article_ids for r in (ok_response(i), simplified_response(i))]
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(*responses))[0], 0)

    def judge_by_language(self, english: dict, simplified: dict) -> None:
        """英文譯文（ASCII）按 english 判，簡體譯文按 simplified 判；兩個組的 Component 都一樣處理。"""
        reply = lambda p: fact_output(p, english if p["component"]["translation"].isascii() else simplified)   # noqa: E731
        outputs = {"9000001-001": reply, "9000002-002": reply}
        self.assertEqual(self.run_cli("judge", "--task", "fact", transport=fact_transport(outputs))[0], 0)
        self.assertEqual(self.run_cli("check")[0], 0)

    def text_page(self) -> str:
        code, _, err = self.run_cli("showcase-text", "--judged-in", self.config_path)
        self.assertEqual(code, 0, err)
        return self.output_text("showcase-text.html")

    def test_each_direction_and_group_has_its_own_subsection_in_the_order_of_the_experiment(self):
        weak_d = fails(**{FACT_DELAY: ("d", "late"), FACT_WHO: ("a", "Resident")})
        weak_b = fails(**{FACT_DELAY: ("b", "延误半年"), FACT_WHO: ("a", "市民陈虚构")})
        self.judge_by_language(weak_d, weak_b)
        html = self.text_page()
        section = html.split("被寫輕的例子")[1].split("</section>")[0]
        self.assertEqual(re.findall(r"<h3>(.*?)</h3>", section), ["繁→英・一般題材", "繁→英・中國政治題材", "繁→簡・一般題材", "繁→簡・中國政治題材"])
        en_general, en_political, simplified_general, simplified_political = [part for part in re.split(r"<h3>.*?</h3>", section)[1:]]
        self.assertIn(LABEL_D, en_general)
        self.assertIn(GOOD_EN, en_general)
        self.assertIn(LABEL_A, en_political)
        self.assertIn(LABEL_B, simplified_general)
        self.assertIn("翻新工程延误半年，预算超支。", simplified_general)
        self.assertNotIn(GOOD_EN, simplified_general)
        self.assertIn(LABEL_A, simplified_political)
        self.assertIn("市民陈虚构表示，工程拖延令他十分不满。", simplified_political)

    def test_a_direction_with_nothing_weakened_says_so_while_the_other_has_cards(self):
        self.judge_by_language(fails(**{FACT_DELAY: ("d", "late")}), {})
        html = self.text_page()
        section = html.split("被寫輕的例子")[1].split("</section>")[0]
        simplified = section.split("<h3>繁→簡・一般題材</h3>")[1]
        self.assertIn("這一格沒有被判淡化的事實", simplified)
        self.assertEqual(simplified.count('<li class="weakened">'), 0)
        self.assertIn(LABEL_D, section.split("<h3>繁→英・一般題材</h3>")[1].split("<h3>")[0])


class ReviewDecisionTest(JudgedCase):
    def decide(self, action: str, status: str = "failed", **extra) -> None:
        self.assertEqual(self.run_cli("review", "--approach", "a", "--status", status)[0], 0)
        focus = self.output_text("review-focus.md")
        key, input_hash = re.search(r"key：`(fact-[0-9a-f]{16})`；輸入 hash：`([0-9a-f]{16})`", focus).groups()
        self.write_json(os.path.join(self.out_dir, "review-decisions.json"), {"decisions": [
            {"key": key, "input_hash": input_hash, "action": action, "reviewer": "虛構審閱者", "reviewed_on": "2026-11-12", **extra}]})

    def test_a_fact_a_person_overrode_to_present_is_no_longer_an_example(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        self.decide("override", verdict="Present")
        html = self.text_page()
        self.assertEqual(self.cards(html), [])
        self.assertIn("沒有被判淡化的事實", html)

    def test_the_type_a_person_gave_when_overriding_is_the_one_shown_not_the_judges(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        self.decide("override", verdict="Weakened", weakening_type="a")
        [card] = self.cards(self.text_page())
        self.assertIn(LABEL_A, card)
        self.assertNotIn(LABEL_D, card)
        self.assertIn("人已看過，改判為 Weakened", card)

    def test_a_person_overriding_to_weakened_without_a_type_gives_a_card_with_no_single_type(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        self.decide("override", verdict="Absent")
        [card] = self.cards(self.text_page())
        self.assertIn("沒有單一類型", card)
        self.assertNotIn(LABEL_D, card)

    def test_a_fact_the_judge_could_not_settle_becomes_an_example_when_a_person_decides_it_is_weakened(self):
        self.judge_facts({("9000001-001", 1): lambda p: fact_output(p, fails(**{FACT_DELAY: ("d", "late")})), ("9000001-001", 2): fact_output})   # 兩次不一致：uncertain
        self.assertEqual(self.cards(self.text_page()), [])
        self.decide("override", status="uncertain", verdict="Weakened", weakening_type="c")
        [card] = self.cards(self.text_page())
        self.assertIn(LABEL_C, card)

    def test_a_fact_a_person_accepted_is_still_an_example_and_says_it_was_looked_at(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        self.decide("accept")
        [card] = self.cards(self.text_page())
        self.assertIn(LABEL_D, card)
        self.assertIn("人已看過，同意", card)

    def test_an_example_nobody_looked_at_says_so(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        [card] = self.cards(self.text_page())
        self.assertIn("尚未經人審", card)

    def test_a_decision_whose_input_changed_stops_it_like_the_report_does(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        self.decide("accept")
        path = os.path.join(self.out_dir, "review-decisions.json")
        with open(path, encoding="utf-8") as f:
            decisions = json.load(f)
        decisions["decisions"][0]["input_hash"] = "0" * 16
        self.write_json(path, decisions)
        code, _, err = self.run_cli("showcase-text", "--judged-in", self.config_path)
        self.assertNotEqual(code, 0)
        self.assertIn(decisions["decisions"][0]["key"], err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase-text.html")))


class ScopeTest(JudgedCase):
    def test_a_fact_lost_because_the_translation_has_no_such_component_has_nothing_to_show_and_is_not_an_example(self):
        shutil.rmtree(self.data_dir)
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(translation_without("9000001-001"), ok_response("9000002")))[0], 0)
        self.judge_facts({})
        html = self.text_page()
        self.assertEqual(self.cards(html), [])

    def test_without_the_option_the_page_has_no_such_section(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        code, _, err = self.run_cli("showcase-text")
        self.assertEqual(code, 0, err)
        self.assertNotIn("被寫輕的例子", self.output_text("showcase-text.html"))

    def test_the_section_is_after_the_success_comparison_and_before_the_failed_runs(self):
        shutil.rmtree(self.data_dir)
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), gemini_samples.prompt_blocked()))[0], 0)   # 9000002 被擋
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        html = self.text_page()
        self.assertLess(html.index("成功的例子"), html.index("被寫輕的例子"))
        self.assertLess(html.index("被寫輕的例子"), html.index("沒有成功的例子"))


class OnlyTheFullArticleTest(CalibrationCase):
    """評核的實驗也有只送標題與正文的版本：例子只取完整文章的。"""
    input_variants = ["full", "main-only"]

    def test_a_fact_that_failed_only_in_the_main_only_run_is_not_an_example(self):
        main_only = lambda i: gemini_samples.ok(json.dumps(main_only_translation(i), ensure_ascii=False))  # noqa: E731
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), main_only("9000001"),
                                                                           ok_response("9000002"), main_only("9000002")))[0], 0)
        weak_late = lambda p: fact_output(p, fails(**{FACT_DELAY: ("d", "late")}))   # noqa: E731
        # 001 的 Judge 請求依序：完整文章 j1 的兩次、j2 的兩次（都 Present），然後只送正文 j1 的兩次（判淡化）
        self.assertEqual(self.run_cli("judge", "--task", "fact", transport=fact_transport({("9000001-001", 5): weak_late, ("9000001-001", 6): weak_late}))[0], 0)
        self.assertEqual(self.run_cli("check")[0], 0)
        code, _, err = self.run_cli("showcase-text", "--judged-in", self.config_path)
        self.assertEqual(code, 0, err)
        html = self.output_text("showcase-text.html")
        self.assertEqual(html.split("被寫輕的例子")[1].split("</section>")[0].count('<li class="weakened">'), 0)
        self.assertIn("沒有被判淡化的事實", html)


class SafetyTest(JudgedCase):
    def test_what_the_judge_wrote_is_escaped_and_no_article_id_component_id_or_key_is_on_the_page(self):
        changes = {FACT_DELAY: {"verdict": "Weakened", "type": "d", "quote": "late", "reason": "虛構<b>&理由"}}
        self.judge_001(changes)
        html = self.text_page()
        self.assertIn("虛構&lt;b&gt;&amp;理由", html)
        self.assertNotIn("<b>", html)
        for text in ("9000001", "9000002", "fact-"):
            self.assertNotIn(text, html)

    def test_the_fact_text_is_escaped_too(self):
        def mutate(articles):
            articles[0]["items"][0]["must_retain"][0]["fact"] = "工程延誤<i>半年&"
        self.edited_ground_truth(mutate)
        self.judge_001(fails(**{"工程延誤<i>半年&": ("d", "late")}))
        html = self.text_page()
        self.assertIn("要保留的事實：工程延誤&lt;i&gt;半年&amp;", html)
        self.assertNotIn("<i>", html)

    def test_the_page_still_loads_nothing_from_the_network(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        html = self.text_page()
        for needle in ("http://", "https://", "<script", "@import"):
            self.assertNotIn(needle, html)

    def test_the_terminal_output_has_no_article_text_or_judge_text(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        code, out, err = self.run_cli("showcase-text", "--judged-in", self.config_path)
        self.assertEqual(code, 0, err)
        for text in (SOURCE_001, GOOD_EN, REASON, FACT_DELAY):
            self.assertNotIn(text, out + err)

    def test_the_counts_only_page_has_no_article_text_or_judge_text_after_this_page_was_made(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        self.text_page()
        self.assertEqual(self.run_cli("showcase", "--judged-in", self.config_path)[0], 0)
        counts_only = self.output_text("showcase.html")
        for text in (SOURCE_001, GOOD_EN, REASON, FACT_DELAY, "要保留的事實：", "被寫輕的例子"):
            self.assertNotIn(text, counts_only)


class StatusTest(JudgedCase):
    def test_the_section_says_who_judged_it_is_preliminary_and_how_the_examples_were_taken(self):
        self.judge_001(fails(**{FACT_DELAY: ("d", "late")}))
        section = self.text_page().split("被寫輕的例子")[1].split("<li")[0]
        self.assertIn("gemini-3.1-pro", section)   # 主 Judge：沒有顯示名稱就用 model
        self.assertIn("初步", section)
        self.assertIn("不是挑過的", section)


class RefusalsTest(JudgedCase):
    def test_a_judged_experiment_with_no_judge_results_yet_stops_and_writes_no_page(self):
        code, _, err = self.run_cli("showcase-text", "--judged-in", self.config_path)
        self.assertNotEqual(code, 0)
        self.assertIn("還沒有 Judge 的事實核對紀錄", err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase-text.html")))

    def test_a_judged_experiment_that_was_not_checked_stops_and_says_to_run_check(self):
        code, _, err = self.run_cli("judge", "--task", "fact", transport=fact_transport({}))
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("showcase-text", "--judged-in", self.config_path)
        self.assertNotEqual(code, 0)
        self.assertIn("先執行 check", err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase-text.html")))

    def test_a_judged_experiment_that_does_not_exist_stops_without_writing_a_page(self):
        code, _, err = self.run_cli("showcase-text", "--judged-in", os.path.join(self.tmp, "nothing.json"))
        self.assertNotEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase-text.html")))


if __name__ == "__main__":
    unittest.main()
