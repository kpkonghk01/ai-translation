"""showcase：給非技術讀者看的 HTML 展示報告。只含計數（模型名、組別、數量），不含文章的任何內容；從 main(argv) 進入。

表格解析成「列」再斷言，不依賴 HTML 的排版。只用虛構資料。"""
import json
import os
import unittest
from html.parser import HTMLParser

import gemini_samples
from fake_transport import FakeTransport
from fixtures import APPROACH, FAKE_KEY, Workspace, good_simplified_translation, main_only_translation, ok_response, source_article
from gate_helpers import ground_truth_for
from judge_helpers import judge_transport

NAME_A = "測試模型甲（虛構公司）"
NAME_B = "測試模型乙（虛構公司）"
IDS = ("9000001", "9000002", "9000003", "9000004")


class _Tables(HTMLParser):
    """HTML 裡每個有 id 的表格 → 列（每格的文字，空白合併）。"""

    def __init__(self):
        super().__init__()
        self.tables, self._current, self._row, self._cell = {}, None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._current = self.tables.setdefault(dict(attrs).get("id", ""), [])
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._current.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data.split()


class _Examples(HTMLParser):
    """每張例子卡（class="example" 的 li）的文字，空白合併。"""

    def __init__(self):
        super().__init__()
        self.cards, self._card, self._depth = [], None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "li" and "example" in (dict(attrs).get("class") or "").split() and self._card is None:
            self._card, self._depth = [], 1
        elif self._card is not None and tag == "li":
            self._depth += 1

    def handle_endtag(self, tag):
        if self._card is not None and tag == "li":
            self._depth -= 1
            if self._depth == 0:
                self.cards.append(" ".join(self._card))
                self._card = None

    def handle_data(self, data):
        if self._card is not None:
            self._card += data.split()


def example_cards(html: str) -> list[str]:
    parser = _Examples()
    parser.feed(html)
    return parser.cards


def card_with(html: str, needle: str) -> str:
    return next(card for card in example_cards(html) if needle in card)


def tables(html: str) -> dict:
    parser = _Tables()
    parser.feed(html)
    return parser.tables


def row_of(table: list, name: str) -> list:
    return next(row for row in table if row[0] == name)


class Showcases:
    """寫出展示頁並讀回來（Workspace 的測試共用）。"""

    def showcase(self) -> str:
        code, out, err = self.run_cli("showcase")
        self.assertEqual(code, 0, err)
        return self.output_text("showcase.html")


class ShowcaseCase(Showcases, Workspace):
    article_ids = IDS

    @property
    def approach_b_path(self) -> str:
        return os.path.join(self.tmp, "approach-b.json")

    def experiment(self) -> dict:
        exp = super().experiment()
        exp["approaches"].append({"config": self.approach_b_path, "runs": 1})
        return exp

    def setUp(self):
        super().setUp()
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A})
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": NAME_B})
        self.use_ground_truth(ground_truth_for(("9000001", "general"), ("9000002", "general"),
                                               ("9000003", "china-political"), ("9000004", "china-political")))

    def translate_all(self, *responses, args=()):
        code, _, err = self.run_cli("translate", *args, transport=FakeTransport(*responses))
        self.assertEqual(code, 0, err)

    def run_the_scenario(self):
        """每篇文章依序是 甲、乙 兩個模型。甲：9000002 被擋、9000004 出錯；乙：9000004 拒絕。"""
        self.translate_all(
            ok_response("9000001"), ok_response("9000001"),
            gemini_samples.prompt_blocked(), ok_response("9000002"),
            ok_response("9000003"), ok_response("9000003"),
            gemini_samples.unknown_format(), gemini_samples.refusal())


class ResultsTableTest(ShowcaseCase):
    def test_one_row_per_model_with_the_display_name_and_the_counts_of_each_result(self):
        self.run_the_scenario()
        table = tables(self.showcase())["results"]
        self.assertEqual(table[0], ["模型", "完成翻譯", "被擋（一般題材）", "被擋（中國政治題材）", "拒絕", "其他未完成"])
        self.assertEqual(row_of(table, NAME_A), [NAME_A, "50% 2/4", "50% 1/2", "0% 0/2", "0% 0/4", "25% 1/4 出錯 1"])
        self.assertEqual(row_of(table, NAME_B), [NAME_B, "75% 3/4", "0% 0/2", "0% 0/2", "25% 1/4", "0% 0/4"])

    def test_what_is_in_other_unfinished_is_written_under_the_number_not_in_a_hover_hint(self):
        self.translate_all(gemini_samples.candidate_with({"role": "model"}), ok_response("9000001"),   # 甲：空內容
                           gemini_samples.unknown_format(), ok_response("9000002"),   # 甲：出錯
                           ok_response("9000003"), ok_response("9000003"), ok_response("9000004"), ok_response("9000004"))
        html = self.showcase()
        table = tables(html)["results"]
        self.assertEqual(row_of(table, NAME_A)[5], "50% 2/4 空內容 1、出錯 1")
        self.assertEqual(row_of(table, NAME_B)[5], "0% 0/4")   # 沒有就不寫
        self.assertNotIn("title=", html)
        self.assertNotIn("滑鼠", html)   # 不再叫人把滑鼠移上去

    def test_without_a_display_name_the_row_shows_the_model_not_the_code(self):
        self.write_json(self.approach_path, APPROACH)
        self.run_the_scenario()
        names = [row[0] for row in tables(self.showcase())["results"][1:]]
        self.assertEqual(names, [APPROACH["model"], NAME_B])

    def test_the_names_are_escaped(self):
        self.write_json(self.approach_path, {**APPROACH, "display_name": "甲<b>&乙"})
        self.run_the_scenario()
        html = self.showcase()
        self.assertIn("甲&lt;b&gt;&amp;乙", html)
        self.assertNotIn("甲<b>", html)

    def test_without_ground_truth_every_article_is_one_group(self):
        self.extra_experiment.pop("ground_truth")
        self.write_json(self.config_path, self.experiment())
        self.run_the_scenario()
        table = tables(self.showcase())["results"]
        self.assertEqual(table[0][2:4], ["被擋（全部文章）", "拒絕"])
        self.assertEqual(row_of(table, NAME_A)[2], "25% 1/4")

    def test_the_summary_lines_name_who_was_blocked_most_and_who_was_not_blocked_at_all(self):
        self.run_the_scenario()
        html = self.showcase()
        self.assertIn(f"被擋最多的是{NAME_A}：25%（1/4）", html)
        self.assertIn(f"完全沒被擋：{NAME_B}", html)
        self.assertIn(f"其他未完成最多的是{NAME_A}：25%（1/4）", html)
        self.assertIn(f"拒絕翻譯（Refusal）最多的是{NAME_B}：25%（1/4）", html)

    def test_when_nothing_was_blocked_it_says_so(self):
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(2)])
        html = self.showcase()
        self.assertIn("沒有任何模型被擋", html)
        self.assertIn("沒有任何模型拒絕翻譯（Refusal）", html)
        self.assertNotIn("被擋最多的是", html)


class RoundingTest(Showcases, Workspace):
    article_ids = ("9000001", "9000002", "9000003")

    def test_percentages_are_rounded_to_the_nearest_whole_number(self):
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A})
        code, _, err = self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked(), gemini_samples.prompt_blocked(),
                                                                         ok_response("9000003")))
        self.assertEqual(code, 0, err)
        row = row_of(tables(self.showcase())["results"], NAME_A)
        self.assertEqual(row[1:], ["33% 1/3", "67% 2/3", "0% 0/3", "0% 0/3"])   # 2/3 是 67%，不是 66%


class ExamplesTest(ShowcaseCase):
    """每種出現過的結果各一張例子卡：取自主表的 Run 裡該種結果的第一個，不是挑過的；只有模型、方向、組別和數量。"""

    def test_one_card_for_each_kind_of_result_that_happened_and_none_for_the_others(self):
        self.run_the_scenario()   # 甲：完成、被擋、完成、出錯；乙：完成、完成、完成、拒絕
        cards = example_cards(self.showcase())
        self.assertEqual(len(cards), 4)
        for needle in ("完成翻譯", "被擋", "拒絕", "出錯"):
            self.assertEqual(sum(needle in card.split("。")[0] for card in cards), 1, needle)
        self.assertFalse(any("空內容" in card or "輸出格式壞" in card for card in cards))

    def test_what_did_not_happen_is_listed_so_the_reader_does_not_wonder(self):
        self.run_the_scenario()
        self.assertIn("這一批沒有出現：空內容、輸出格式壞、截斷", self.showcase())

    def test_a_card_names_the_model_direction_and_group_of_the_first_such_run_and_how_many_there_were(self):
        self.run_the_scenario()   # 只有甲在 9000002（一般題材）被擋
        card = card_with(self.showcase(), "被擋")
        for expected in (NAME_A, "繁→英", "一般題材", "這一批共 1 個 Run", "原因代碼 prompt:PROHIBITED_CONTENT"):
            self.assertIn(expected, card)

    def test_the_first_such_run_is_the_example_not_a_chosen_one(self):
        # 乙先在 9000001（一般題材）被擋，甲後在 9000003（中國政治題材）被擋：例子是 Run 紀錄裡第一個
        self.translate_all(gemini_samples.prompt_blocked(), ok_response("9000001"),   # 9000001：甲被擋、乙成功
                           ok_response("9000002"), ok_response("9000002"),
                           ok_response("9000003"), gemini_samples.prompt_blocked(),   # 9000003：乙被擋
                           ok_response("9000004"), ok_response("9000004"))
        card = card_with(self.showcase(), "被擋")
        self.assertIn("這一批共 2 個 Run", card)
        self.assertIn(f"{NAME_A} 1、{NAME_B} 1", card)
        self.assertIn(f"發生在：{NAME_A}", card)
        self.assertIn("一般題材", card)

    def test_different_block_reasons_each_get_their_own_card(self):
        self.translate_all(gemini_samples.prompt_blocked(), gemini_samples.safety_stopped("{}", "PROHIBITED_CONTENT"),
                           ok_response("9000002"), ok_response("9000002"), ok_response("9000003"), ok_response("9000003"),
                           ok_response("9000004"), ok_response("9000004"))
        html = self.showcase()
        self.assertIn("原因代碼 prompt:PROHIBITED_CONTENT", card_with(html, "輸入端"))
        self.assertIn("原因代碼 output:PROHIBITED_CONTENT", card_with(html, "翻到一半"))
        self.assertEqual(sum("被擋" in card.split("。")[0] for card in example_cards(html)), 2)

    def test_the_success_card_says_how_many_segments_the_translation_has(self):
        self.run_the_scenario()
        segments = len(source_article("9000001")["components"])
        self.assertIn(f"譯文有 {segments} 個段落", card_with(self.showcase(), "完成翻譯"))

    def test_the_text_of_a_card_comes_from_the_kind_of_result_and_never_from_the_provider(self):
        self.run_the_scenario()   # 甲有一個 error：它的 reason 是 provider 回應裡來的字串
        html = self.showcase()
        self.assertNotIn("HTTP", html)
        self.assertNotIn("finishReason", html)

    def test_an_example_never_shows_an_article_id_or_the_unit_that_failed(self):
        self.run_the_scenario()
        for article_id in IDS:
            self.assertNotIn(article_id, self.showcase())

    def test_it_is_the_same_page_every_time(self):
        self.run_the_scenario()
        first = self.showcase()
        self.assertEqual(self.showcase().split("資料截至")[0], first.split("資料截至")[0])


class ExamplesScopeTest(Showcases, Workspace):
    """例子與主表同一批 Run：完整文章的第 1 次。只在重複跑裡出現的結果，不是主表的數字，也不該出現在例子。"""
    article_ids = ("9000001", "9000002")
    runs = 2

    def setUp(self):
        super().setUp()
        self.use_ground_truth(ground_truth_for(("9000001", "general"), ("9000002", "general")))

    def test_a_result_that_only_happened_in_a_repeat_has_no_card(self):
        code, _, err = self.run_cli("translate", transport=FakeTransport(
            ok_response("9000001"), gemini_samples.prompt_blocked(), ok_response("9000002"), ok_response("9000002")))   # 9000001 的第 2 次被擋
        self.assertEqual(code, 0, err)
        html = self.showcase()
        self.assertFalse(any("被擋" in card.split("。")[0] for card in example_cards(html)))
        self.assertIn("被擋", html.split("這一批沒有出現：")[1].split("。")[0])


class ExamplesSafetyTest(ShowcaseCase):
    def test_a_block_reason_that_is_not_a_plain_code_is_not_shown_and_the_card_still_exists(self):
        self.translate_all(gemini_samples.prompt_blocked("SOMETHING ODD HERE"), ok_response("9000001"),
                           ok_response("9000002"), ok_response("9000002"), ok_response("9000003"), ok_response("9000003"),
                           ok_response("9000004"), ok_response("9000004"))
        html = self.showcase()
        card = card_with(html, "被擋")
        self.assertIn("被擋（其他）", card)   # 代碼不是單純的寫法：連前綴也不信，只說被擋
        self.assertNotIn("原因代碼", card)
        self.assertNotIn("ODD", html)

    def test_model_names_inside_a_card_are_escaped(self):
        self.write_json(self.approach_path, {**APPROACH, "display_name": "甲<i>&乙"})
        self.run_the_scenario()
        html = self.showcase()
        self.assertIn("發生在：甲&lt;i&gt;&amp;乙", html)   # 對原始 HTML 斷言：解析器會把 &lt; 還原
        self.assertNotIn("甲<i>", html)

    def test_a_sample_made_from_another_experiment_is_not_mentioned(self):
        self.run_the_scenario()
        elsewhere = os.path.join(self.data_dir, "experiments", "somewhere-else")
        os.makedirs(elsewhere)
        self.write_json(os.path.join(elsewhere, "sample.json"), {"from": "not-this-one", "seed": 1, "per_group": 1, "controls": 0, "articles": ["x"]})
        html = self.showcase()
        self.assertNotIn("抽樣", html)
        self.assertIn("沒有設定 Judge", html)


class ProgressTest(ShowcaseCase):
    def test_an_unfinished_experiment_says_so_and_a_finished_one_does_not(self):
        self.translate_all(ok_response("9000001"), ok_response("9000001"), ok_response("9000002"), args=("--limit", "3"))
        html = self.showcase()
        self.assertIn("已完成 3／8 個 Run", html)
        self.assertIn("所有模型合計", html)
        self.assertIn("仍在進行", html)
        self.assertNotIn("已全部跑完", html)
        self.translate_all(ok_response("9000002"), *[ok_response(i) for i in IDS[2:] for _ in range(2)])
        html = self.showcase()
        self.assertIn("已全部跑完：8 個 Run", html)
        self.assertNotIn("仍在進行", html)

    def test_a_run_that_is_still_without_a_result_after_a_redo_is_not_waiting_for_a_redo(self):
        self.run_the_scenario()   # 甲在 9000004 出錯
        self.run_cli("translate", "--redo-errors", transport=FakeTransport(gemini_samples.unknown_format()))   # 重做仍然出錯
        html = self.showcase()
        self.assertIn("已全部跑完：8 個 Run", html)
        self.assertIn("1 個 Run 重做過仍然沒有結果（出錯或空內容），算在「其他未完成」", html)
        self.assertNotIn("尚待重做", html)
        self.assertNotIn("仍在進行", html)
        self.assertEqual(row_of(tables(html)["results"], NAME_A)[5], "25% 1/4 出錯 1")   # 仍算在其他未完成

    def test_a_redone_run_that_still_fails_counts_as_done_while_the_experiment_is_still_running(self):
        self.translate_all(ok_response("9000001"), gemini_samples.unknown_format(), ok_response("9000002"), args=("--limit", "3"))   # 乙在 9000001 出錯
        self.run_cli("translate", "--redo-errors", "--limit", "1", transport=FakeTransport(gemini_samples.unknown_format()))   # 重做，仍然出錯
        html = self.showcase()
        self.assertIn("已完成 3／8 個 Run", html)   # 重做過、再做也不會變：不算還沒完成
        self.assertIn("1 個 Run 重做過仍然沒有結果", html)
        self.assertNotIn("尚待重做", html)

    def test_a_run_that_was_redone_and_still_fails_does_not_hide_one_that_has_not_been_redone_yet(self):
        self.run_the_scenario()
        self.run_cli("translate", "--redo-errors", transport=FakeTransport(gemini_samples.unknown_format()))   # 甲：重做過、仍出錯
        self.rewrite_experiment(lambda e: e["approaches"][1].update(runs=2))   # 乙多一輪，其中一個新的 Run 出錯、還沒重做
        self.translate_all(gemini_samples.unknown_format(), *[ok_response(i) for i in IDS[1:]])
        html = self.showcase()
        self.assertIn("已全部跑過一遍，但有 1 個 Run 出錯或空內容，尚待重做", html)
        self.assertIn("1 個 Run 重做過仍然沒有結果", html)
        self.assertNotIn("已全部跑完", html)

    def test_a_run_that_errored_is_not_counted_as_done_while_the_experiment_is_still_running(self):
        self.translate_all(ok_response("9000001"), gemini_samples.unknown_format(), ok_response("9000002"), args=("--limit", "3"))
        html = self.showcase()
        self.assertIn("已完成 2／8 個 Run", html)   # 3 個 Run 有紀錄，其中 1 個出錯、還沒有結果
        self.assertIn("另有 1 個 Run 出錯或空內容，尚待重做", html)

    def test_when_every_run_has_been_made_but_some_still_need_a_redo_it_does_not_say_finished(self):
        self.run_the_scenario()   # 甲有一個 error
        html = self.showcase()
        self.assertIn("已全部跑過一遍", html)
        self.assertIn("尚待重做", html)
        self.assertNotIn("已全部跑完", html)
        self.assertNotIn("仍在進行", html)
        self.run_cli("translate", "--redo-errors", transport=FakeTransport(ok_response("9000004")))
        html = self.showcase()
        self.assertIn("已全部跑完：8 個 Run", html)
        self.assertNotIn("尚待重做", html)


class ScopeTest(ShowcaseCase):
    def test_runs_made_with_another_config_are_counted_and_said_so_but_a_new_name_is_not_another_config(self):
        self.run_the_scenario()
        self.assertNotIn("不同的設定", self.showcase())
        self.write_json(self.approach_path, {**APPROACH, "display_name": "改了名稱的模型甲"})
        self.assertNotIn("不同的設定", self.showcase())
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A, "max_output_tokens": 1000})
        self.assertIn("4 個 Run 是用跟現在不同的設定跑的", self.showcase())

    def test_runs_of_an_article_that_is_no_longer_in_the_experiment_are_left_out_and_said_so(self):
        self.run_the_scenario()
        self.article_ids = IDS[:3]
        self.write_json(self.config_path, self.experiment())
        html = self.showcase()
        self.assertIn("另有 2 個 Run 屬於目前設定裡已沒有的模型或文章", html)
        self.assertEqual(row_of(tables(html)["results"], NAME_A)[1], "67% 2/3")

    def test_a_group_named_like_a_built_in_label_is_just_a_group(self):
        self.use_ground_truth(ground_truth_for(("9000001", "general"), ("9000002", "general"), ("9000003", "china-political"),
                                               ("9000004", "china-political")), group_overrides={"9000004": "all"})
        self.run_the_scenario()
        table = tables(self.showcase())["results"]
        self.assertEqual(table[0], ["模型", "完成翻譯", "被擋（一般題材）", "被擋（中國政治題材）", "被擋（all）", "拒絕", "其他未完成"])
        self.assertEqual(row_of(table, NAME_A)[4], "0% 0/1")

    def test_an_article_without_ground_truth_is_its_own_group(self):
        self.use_ground_truth(ground_truth_for(("9000001", "general"), ("9000002", "general"), ("9000003", "china-political")))
        self.run_the_scenario()
        html = self.showcase()
        table = tables(html)["results"]
        self.assertEqual(table[0][2:5], ["被擋（一般題材）", "被擋（中國政治題材）", "被擋（沒有 ground truth 的文章）"])
        self.assertIn("沒有 ground truth 的文章 1 篇", html)

    def test_a_ground_truth_that_does_not_validate_stops_it_without_writing_a_page(self):
        self.run_the_scenario()
        self.use_ground_truth(ground_truth_for(("9000001", "general"), ("9000001", "general")))   # 同一篇出現兩次
        code, out, err = self.run_cli("showcase")
        self.assertNotEqual(code, 0)
        self.assertIn("出現了 2 次", err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase.html")))


class DetailTest(ShowcaseCase):
    def test_the_detail_table_has_every_result_class_per_model_direction_and_input_version(self):
        self.run_the_scenario()
        details = tables(self.showcase())["details"]
        self.assertEqual(details[0], ["模型", "方向", "輸入版本", "Run 數", "完成翻譯", "被擋", "拒絕", "輸出格式壞", "截斷", "空內容", "出錯"])
        self.assertEqual(row_of(details, NAME_A), [NAME_A, "繁→英", "完整文章", "4", "2", "1", "0", "0", "0", "0", "1"])
        self.assertEqual(row_of(details, NAME_B), [NAME_B, "繁→英", "完整文章", "4", "3", "0", "1", "0", "0", "0", "0"])


class RepeatedRunsTest(Showcases, Workspace):
    """甲每篇每個方向跑 2 次，另有只送標題與正文的版本；主表只看完整文章的第 1 次，重複與只送正文放在另一張表。"""
    article_ids = ("9000001", "9000002")
    runs = 2
    input_variants = ["full", "main-only"]

    def setUp(self):
        super().setUp()
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A})
        self.use_ground_truth(ground_truth_for(("9000001", "general"), ("9000002", "general")))

    def main_only(self, article_id):
        return gemini_samples.ok(json.dumps(main_only_translation(article_id), ensure_ascii=False))

    def prepare(self):
        # 9000001：完整文章第 1 次被擋、第 2 次成功，只送正文都成功；9000002 全部成功
        responses = [gemini_samples.prompt_blocked(), ok_response("9000001"), self.main_only("9000001"), self.main_only("9000001"),
                     ok_response("9000002"), ok_response("9000002"), self.main_only("9000002"), self.main_only("9000002")]
        code, _, err = self.run_cli("translate", transport=FakeTransport(*responses))
        self.assertEqual(code, 0, err)
        return self.showcase()

    def test_the_main_table_counts_only_the_first_run_of_the_full_article(self):
        row = row_of(tables(self.prepare())["results"], NAME_A)
        self.assertEqual(row[1:], ["50% 1/2", "50% 1/2", "0% 0/2", "0% 0/2"])

    def test_the_repeats_and_the_title_and_body_only_version_have_their_own_table(self):
        table = tables(self.prepare())["stability"]
        self.assertEqual(table[0], ["模型", "每篇每個方向跑幾次", "完整文章：被擋的 Run", "完整文章：至少被擋一次的文章", "只送標題與正文：被擋的 Run"])
        self.assertEqual(row_of(table, NAME_A), [NAME_A, "2", "25% 1/4", "50% 1/2", "0% 0/4"])

    def test_the_detail_table_has_a_row_for_each_input_version(self):
        details = tables(self.prepare())["details"]
        self.assertEqual([row[2:5] for row in details[1:]], [["完整文章", "4", "3"], ["只送標題與正文", "4", "4"]])


class NoStabilityTableTest(ShowcaseCase):
    def test_a_model_run_once_on_the_full_article_has_no_such_table(self):
        self.run_the_scenario()
        self.assertNotIn("stability", tables(self.showcase()))


class SectionsTest(ShowcaseCase):
    def test_it_says_what_was_tested_and_how_many_articles_are_in_each_group(self):
        self.run_the_scenario()
        html = self.showcase()
        self.assertIn("一般題材 2 篇", html)
        self.assertIn("中國政治題材 2 篇", html)
        self.assertIn(NAME_A, html)

    def test_each_model_says_how_many_runs_it_has_and_where_the_number_comes_from(self):
        self.run_the_scenario()
        html = self.showcase()
        self.assertEqual(html.count("共 4 個 Run（4 篇 × 1 個方向 × 1 次）"), 2)   # 兩個模型各一次

    def test_the_ground_truth_status_is_said_where_the_groups_come_from(self):
        self.run_the_scenario()
        self.assertIn("ground truth 目前是 draft", self.showcase())
        self.extra_experiment.pop("ground_truth")
        self.write_json(self.config_path, self.experiment())
        self.assertNotIn("ground truth", self.showcase())

    def test_a_baseline_is_listed_with_its_display_name_and_is_not_a_row_of_the_results(self):
        self.extra_experiment["baselines"] = [{"id": "e", "dir": os.path.join(self.tmp, "baseline"), "direction": "trad-to-simplified",
                                               "display_name": "現行<i>服務</i>（虛構）"}]
        self.directions = ["trad-to-en", "trad-to-simplified"]
        self.write_json(self.config_path, self.experiment())
        for article_id in IDS:
            self.write_baseline(os.path.join(self.tmp, "baseline"), article_id, good_simplified_translation(article_id))
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(4)])   # 兩個模型 × 兩個方向
        self.assertEqual(self.run_cli("import-baseline")[0], 0)
        html = self.showcase()
        self.assertIn("現行&lt;i&gt;服務&lt;/i&gt;（虛構）", html)
        self.assertNotIn("<i>", html)
        self.assertEqual([row[0] for row in tables(html)["results"][1:]], [NAME_A, NAME_B])
        self.assertIn("已全部跑完：16 個 Run", html)   # 匯入的 baseline（4 個）不算翻譯的進度
        self.assertEqual(len(tables(html)["details"]) - 1, 4)   # 細節表也只有兩個模型 × 兩個方向

    def test_without_a_judge_it_says_the_softening_rate_and_the_quality_are_not_measured_yet(self):
        self.run_the_scenario()
        self.assertIn("沒有設定 Judge", self.showcase())

    def test_a_sample_experiment_made_from_it_is_mentioned_as_where_the_judging_happens(self):
        self.run_the_scenario()
        self.assertNotIn("抽樣", self.showcase())
        code, _, err = self.run_cli("sample", "--id", "tracer-sample", "--per-group", "1", "--controls", "0")
        self.assertEqual(code, 0, err)
        html = self.showcase()
        self.assertIn("淡化核對在抽樣的實驗上做（2 篇文章）", html)
        self.assertNotIn("沒有設定 Judge", html)

    def test_with_a_judge_that_has_not_run_yet_it_says_there_is_no_result_yet(self):
        self.use_judging()
        self.run_the_scenario()
        html = self.showcase()
        self.assertIn("已設定 Judge，但還沒有評核結果", html)
        self.assertNotIn("沒有設定 Judge", html)
        self.assertNotIn("report.md", html)

    def test_with_judge_results_it_says_they_are_not_on_this_page(self):
        self.use_judging()
        self.run_the_scenario()
        code, _, err = self.run_cli("judge", "--task", "mqm", transport=judge_transport())
        self.assertEqual(code, 0, err)
        html = self.showcase()
        self.assertIn("評核結果沒有放在這一頁", html)
        self.assertNotIn("已設定 Judge，但還沒有評核結果", html)


class SafetyTest(ShowcaseCase):
    def test_the_page_has_counts_only_no_article_text_no_ids_and_no_key(self):
        self.run_the_scenario()
        html = self.showcase()
        for secret in (source_article("9000001")["title"], "9000001", "9000004", FAKE_KEY):
            self.assertNotIn(secret, html)

    def test_the_page_loads_nothing_from_the_network(self):
        self.run_the_scenario()
        html = self.showcase()
        for needle in ("http://", "https://", "<script", "@import"):
            self.assertNotIn(needle, html)

    def test_no_runs_is_a_clean_failure_that_writes_nothing(self):
        code, out, err = self.run_cli("showcase")
        self.assertNotEqual(code, 0)
        self.assertIn("沒有 Run 紀錄", err)
        self.assertEqual(out, "")
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase.html")))


class DisplayNameConfigTest(ShowcaseCase):
    def test_a_display_name_does_not_change_the_config_hash(self):
        self.write_json(self.approach_path, APPROACH)
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b"})
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(2)])
        plain = {r["approach"]: r["config_hash"] for r in self.read_runs()}
        os.remove(os.path.join(self.out_dir, "runs.jsonl"))
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A})
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": NAME_B})
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(2)])
        self.assertEqual({r["approach"]: r["config_hash"] for r in self.read_runs()}, plain)

    def test_two_models_with_the_same_name_are_refused(self):
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": NAME_A})
        self.assert_fails_cleanly("顯示名稱重覆")

    def assert_showcase_refuses_the_names(self):
        self.translate_all(*[ok_response(i) for i in IDS for _ in range(2)])   # 翻譯本身不在乎名稱
        code, out, err = self.run_cli("showcase")
        self.assertNotEqual(code, 0)
        self.assertIn("顯示名稱重覆", err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "showcase.html")))

    def test_a_name_that_is_another_models_fallback_is_refused_by_showcase(self):
        # 甲沒有顯示名稱時，列出來的名字是它的 model；乙取了同一個字串，兩列會分不出來
        self.write_json(self.approach_path, APPROACH)
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": APPROACH["model"]})
        self.assert_showcase_refuses_the_names()

    def test_two_models_without_names_and_with_the_same_model_are_refused_by_showcase(self):
        self.write_json(self.approach_path, APPROACH)
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b"})
        self.assert_showcase_refuses_the_names()

    def test_names_that_differ_only_in_case_or_spacing_are_the_same_name(self):
        self.write_json(self.approach_path, {**APPROACH, "display_name": "Model One"})
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": "model  one"})
        self.assert_fails_cleanly("顯示名稱重覆")

    def test_a_blank_name_is_refused(self):
        self.write_json(self.approach_b_path, {**APPROACH, "id": "b", "display_name": "  "})
        self.assert_fails_cleanly("display_name")


if __name__ == "__main__":
    unittest.main()
