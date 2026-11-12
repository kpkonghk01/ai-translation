"""showcase-text：含原文與譯文片段的展示頁（只在本機看）。從 main(argv) 進入。

和 showcase（只有計數、可以轉給別人）是兩個檔案：這一頁有文章內容，所以 showcase 的頁面不能因為它而有任何文章內容。
只用虛構資料。"""
import json
import os
import unittest

import gemini_samples
from fake_transport import FakeTransport
from fixtures import APPROACH, FAKE_KEY, Workspace
from translation_eval import cli

NAME_A = "測試模型甲（虛構公司）"
NAME_B = "測試模型乙（虛構公司）"
IDS = ("9000001", "9000002", "9000003")
LONG = "長" * 200          # 超過 160 字：不是好的片段
MID = "中段內容" + "文" * 56   # 60 字：合適的片段
SHORT = "短"


def article(article_id: str, title: str, paragraphs: list[str]) -> dict:
    return {"title": title, "mainChannel": "社會新聞", "tags": ["虛構"],
            "components": [{"id": f"{article_id}-{n:03d}", "type": "paragraph", "text": text} for n, text in enumerate(paragraphs, 1)],
            "meta": {"source": "public-web", "articleId": int(article_id), "unknown": [], "warnings": []}}


def translated(source: dict) -> dict:
    """假的譯文：標題前面加 T:，每段前面加 EN:。"""
    return {"title": "T:" + source["title"], "tags": ["Fictional"],
            "components": [{**c, "text": "EN:" + c["text"]} for c in source["components"]]}


class TextCase(Workspace):
    article_ids = IDS

    def experiment(self) -> dict:
        exp = super().experiment()
        exp["approaches"].append({"config": os.path.join(self.tmp, "approach-b.json"), "runs": 1})
        return exp

    def setUp(self):
        super().setUp()
        self.sources = {i: article(i, f"標題{n}", [LONG, MID + str(n), SHORT]) for n, i in enumerate(IDS, 1)}
        self.write_articles()
        self.write_json(self.approach_path, {**APPROACH, "display_name": NAME_A})
        self.write_json(os.path.join(self.tmp, "approach-b.json"), {**APPROACH, "id": "b", "display_name": NAME_B})
        # 組別與角色就夠了：這一頁不看敏感句，而 ground_truth_for 的句子是對 source_article 寫的，不是這裡的文章
        self.use_ground_truth({"articles": [
            {"article_id": "9000001", "role": "sensitive", "domain": "general", "note": "虛構", "items": [], "source_defects": []},
            {"article_id": "9000002", "role": "sensitive", "domain": "china-political", "note": "虛構", "items": [], "source_defects": []},
            {"article_id": "9000003", "role": "control", "domain": "general", "note": "虛構的對照", "items": [], "source_defects": []}]})

    def write_articles(self) -> None:
        for article_id, source in self.sources.items():
            self.write_json(os.path.join(self.articles_dir, f"{article_id}.json"), source)

    def ok(self, article_id: str):
        return gemini_samples.ok(json.dumps(translated(self.sources[article_id]), ensure_ascii=False))

    def translate_all(self, *responses):
        code, _, err = self.run_cli("translate", transport=FakeTransport(*responses))
        self.assertEqual(code, 0, err)

    def all_ok(self):
        self.translate_all(*[self.ok(i) for i in IDS for _ in range(2)])

    def scenario(self):
        """每篇依序是 甲、乙。甲：9000002 被擋；乙：9000001 空內容。其餘成功。"""
        self.translate_all(self.ok("9000001"), gemini_samples.candidate_with({"role": "model"}),
                           gemini_samples.prompt_blocked(), self.ok("9000002"),
                           self.ok("9000003"), self.ok("9000003"))

    @property
    def text_page_path(self) -> str:
        return os.path.join(self.out_dir, "showcase-text.html")

    def text_page(self) -> str:
        code, out, err = self.run_cli("showcase-text")
        self.assertEqual(code, 0, err)
        return self.output_text("showcase-text.html")


class SuccessComparisonTest(TextCase):
    def test_the_success_example_shows_every_model_translating_the_same_passage_of_a_control_article(self):
        self.all_ok()
        html = self.text_page()
        self.assertIn("標題3", html)                         # 對照文章（不敏感的）
        self.assertNotIn("標題1", html)
        self.assertIn(MID + "3", html)                       # 原文片段
        for name in (NAME_A, NAME_B):
            self.assertIn(name, html)
        self.assertEqual(html.count("EN:" + MID + "3"), 2)  # 兩個模型各一份譯文
        self.assertEqual(html.count("T:標題3"), 2)

    def test_the_passage_is_a_paragraph_of_a_reasonable_length_not_the_first_one(self):
        self.all_ok()
        html = self.text_page()
        self.assertNotIn("長" * 50, html)   # 第一段太長，不選
        self.assertNotIn("EN:" + SHORT, html)

    def test_when_no_paragraph_is_a_good_length_the_first_one_is_cut_and_marked(self):
        self.sources["9000003"] = article("9000003", "標題3", [LONG, LONG])
        self.write_articles()
        self.all_ok()
        html = self.text_page()
        self.assertIn("長" * 160 + "…", html)
        self.assertNotIn("長" * 161, html)

    def test_a_short_only_article_uses_its_first_paragraph_as_it_is(self):
        self.sources["9000003"] = article("9000003", "標題3", [SHORT, "甲"])
        self.write_articles()
        self.all_ok()
        html = self.text_page()
        self.assertIn(">" + SHORT + "<", html)
        self.assertNotIn("…", html)

    def test_a_model_that_did_not_succeed_on_that_article_is_not_in_the_comparison_and_another_article_is_used(self):
        # 對照文章 9000003 乙失敗：換成兩個模型都成功的文章（9000002、9000001 之中第一個）
        self.translate_all(self.ok("9000001"), self.ok("9000001"), self.ok("9000002"), self.ok("9000002"),
                           self.ok("9000003"), gemini_samples.prompt_blocked())
        html = self.text_page()
        self.assertEqual(html.count("EN:" + MID + "1"), 2)
        self.assertNotIn("EN:" + MID + "3", html)

    def test_without_ground_truth_the_first_article_that_every_model_translated_is_used(self):
        self.extra_experiment.pop("ground_truth")
        self.write_json(self.config_path, self.experiment())
        self.all_ok()
        self.assertEqual(self.text_page().count("EN:" + MID + "1"), 2)

    def test_the_comparison_uses_the_first_direction_of_the_experiment(self):
        self.directions = ["trad-to-en", "trad-to-simplified"]
        self.write_json(self.config_path, self.experiment())
        # 每篇依序：甲繁→英、甲繁→簡、乙繁→英、乙繁→簡
        english, simplified = self.ok, lambda i: gemini_samples.ok(json.dumps(
            {**translated(self.sources[i]), "components": [{**c, "text": "SC:" + c["text"][3:]} for c in translated(self.sources[i])["components"]]},
            ensure_ascii=False))
        self.translate_all(*[response for i in IDS for response in (english(i), simplified(i), english(i), simplified(i))])
        html = self.text_page()
        comparison = html.split("成功的例子")[1].split("</section>")[0]
        self.assertIn("繁→英", comparison)
        self.assertIn("EN:" + MID + "3", comparison)
        self.assertNotIn("SC:", comparison)

    def test_a_model_whose_translation_lacks_the_passage_is_shown_as_lacking_it(self):
        lacking = translated(self.sources["9000003"])
        lacking["components"] = [c for c in lacking["components"] if c["id"] != "9000003-002"]   # 乙的譯文漏了被選中的那一段
        self.translate_all(self.ok("9000001"), self.ok("9000001"), self.ok("9000002"), self.ok("9000002"),
                           self.ok("9000003"), gemini_samples.ok(json.dumps(lacking, ensure_ascii=False)))
        html = self.text_page()
        self.assertEqual(html.count("EN:" + MID + "3"), 1)   # 只有甲有這一段
        self.assertIn("這個模型的譯文沒有這一段", html)


class FailureExamplesTest(TextCase):
    def test_a_failure_card_shows_the_source_passage_of_the_first_such_run_and_says_what_came_back(self):
        self.scenario()
        html = self.text_page()
        blocked = html.split("被擋（輸入端）")[1].split("<li class=\"example\"")[0]
        self.assertIn("標題2", blocked)
        self.assertIn(MID + "2", blocked)
        self.assertIn("沒有任何譯文", blocked)
        empty = html.split("<h3>空內容</h3>")[1].split("<li class=\"example\"")[0]
        self.assertIn("標題1", empty)
        self.assertIn("回來的是空的", empty)

    def test_the_cards_keep_what_the_counts_only_page_has(self):
        self.scenario()
        html = self.text_page()
        for expected in (NAME_A, "原因代碼 prompt:PROHIBITED_CONTENT", "這一批共 1 個 Run", "一般題材", "中國政治題材"):
            self.assertIn(expected, html)

    def test_what_the_provider_sent_back_is_never_put_on_the_page(self):
        self.scenario()
        html = self.text_page()
        self.assertNotIn("HTTP", html)
        self.assertNotIn("candidates", html)

    def test_only_kinds_that_happened_get_a_card(self):
        self.all_ok()
        html = self.text_page()
        for heading in ("被擋", "空內容", "輸出格式壞", "出錯"):
            self.assertNotIn(f"<h3>{heading}", html)


class PageTest(TextCase):
    def test_the_page_says_it_has_article_text_and_is_for_this_machine_only(self):
        self.all_ok()
        html = self.text_page()
        self.assertIn("含真實文章的原文與譯文", html)
        self.assertIn("只在本機看，不要轉發", html)
        self.assertIn("showcase.html", html)   # 要給別人看的是那一頁

    def test_text_is_escaped_and_no_article_id_or_key_is_on_the_page(self):
        self.sources["9000003"] = article("9000003", "標題<b>&3", [MID + "<i>"])
        self.write_articles()
        self.all_ok()
        html = self.text_page()
        self.assertIn("標題&lt;b&gt;&amp;3", html)
        self.assertNotIn("<b>", html)
        self.assertNotIn("<i>", html)
        for article_id in IDS:
            self.assertNotIn(article_id, html)
        self.assertNotIn(FAKE_KEY, html)

    def test_the_page_loads_nothing_from_the_network(self):
        self.all_ok()
        html = self.text_page()
        for needle in ("http://", "https://", "<script", "@import"):
            self.assertNotIn(needle, html)

    def test_the_terminal_output_has_no_article_text(self):
        self.all_ok()
        code, out, err = self.run_cli("showcase-text")
        self.assertEqual(code, 0, err)
        for text in ("標題", MID, "EN:", "T:"):
            self.assertNotIn(text, out + err)
        self.assertIn("showcase-text.html", out)


class SeparationTest(TextCase):
    def test_the_counts_only_page_still_has_no_article_text_after_the_text_page_was_made(self):
        self.all_ok()
        self.text_page()
        self.assertEqual(self.run_cli("showcase")[0], 0)
        counts_only = self.output_text("showcase.html")
        for text in ("標題", MID, "EN:", "T:", "含真實文章"):
            self.assertNotIn(text, counts_only)

    def test_making_the_counts_only_page_does_not_write_the_text_page(self):
        self.all_ok()
        self.assertEqual(self.run_cli("showcase")[0], 0)
        self.assertFalse(os.path.exists(self.text_page_path))


class RefusalsTest(TextCase):
    def test_no_runs_is_a_clean_failure_that_writes_nothing(self):
        code, out, err = self.run_cli("showcase-text")
        self.assertNotEqual(code, 0)
        self.assertIn("沒有 Run 紀錄", err)
        self.assertEqual(out, "")
        self.assertFalse(os.path.exists(self.text_page_path))

    def test_a_missing_translation_file_stops_it_and_leaves_an_old_page_alone(self):
        self.all_ok()
        self.text_page()
        before = self.output_text("showcase-text.html")
        victim = next(r for r in self.read_runs() if r["article_id"] == "9000003" and r["approach"] == "a")
        os.remove(os.path.join(self.out_dir, victim["translation"]))
        code, out, err = self.run_cli("showcase-text")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.output_text("showcase-text.html"), before)

    def test_a_missing_article_file_stops_it_cleanly(self):
        self.all_ok()
        os.remove(os.path.join(self.articles_dir, "9000003.json"))
        code, _, err = self.run_cli("showcase-text")
        self.assertNotEqual(code, 0)
        self.assertFalse(os.path.exists(self.text_page_path))

    def test_display_names_that_clash_are_refused_like_in_the_other_page(self):
        self.all_ok()
        self.write_json(os.path.join(self.tmp, "approach-b.json"), {**APPROACH, "id": "b", "display_name": NAME_A})
        code, _, err = self.run_cli("showcase-text")
        self.assertNotEqual(code, 0)
        self.assertIn("顯示名稱重覆", err)


if __name__ == "__main__":
    unittest.main()
