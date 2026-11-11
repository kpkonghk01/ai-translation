"""輸入版本（full／main-only）：Run key、只送 Main component、check 的預期 Component、Block 誘因實驗的報告。"""
import json
import os
import unittest

import gemini_samples
from fake_transport import FakeTransport
from fixtures import Workspace, main_only_translation, ok_response, source_article


def main_only_response(article_id="9000001"):
    return gemini_samples.ok(json.dumps(main_only_translation(article_id), ensure_ascii=False))


def sent_payload(request):
    return json.loads(json.loads(request.body)["contents"][0]["parts"][0]["text"])


class MainOnlyRequestTest(Workspace):
    input_variants = ["main-only"]

    def sent(self):
        transport = FakeTransport(main_only_response())
        self.assertEqual(self.run_cli("translate", transport=transport)[0], 0)
        return transport.requests[0]

    def test_only_the_main_components_and_the_title_are_sent(self):
        article = source_article()
        payload = sent_payload(self.sent())
        self.assertEqual(payload["title"], article["title"])
        self.assertEqual([c["id"] for c in payload["components"]], ["9000001-001", "9000001-002"])   # highlight、paragraph
        self.assertEqual(payload["tags"], [])   # tags 是 Peripheral component，不送

    def test_the_run_record_says_which_input_variant_it_is(self):
        self.sent()
        [record] = self.read_runs()
        self.assertEqual(record["input_variant"], "main-only")

    def test_a_term_that_only_occurs_in_a_component_that_is_not_sent_is_not_injected(self):
        # 「施工中」只出現在圖說，main-only 不送圖說，模型沒看到它，就不要要求它的譯法
        self.use_term_list({"terms": [{"source": "施工中", "category": "place", "translations": {"trad-to-en": ["Under Construction"]}}]})
        request = self.sent()
        self.assertNotIn("Under Construction", json.loads(request.body)["systemInstruction"]["parts"][0]["text"])


class BothVariantsTest(Workspace):
    article_ids = ("9000001", "9000002")
    input_variants = ["full", "main-only"]

    def test_each_variant_is_its_own_run_with_its_own_files(self):
        transport = FakeTransport(ok_response("9000001"), main_only_response("9000001"), ok_response("9000002"), main_only_response("9000002"))
        self.assertEqual(self.run_cli("translate", transport=transport)[0], 0)
        records = self.read_runs()
        self.assertEqual(sorted((r["article_id"], r["input_variant"]) for r in records),
                         [("9000001", "full"), ("9000001", "main-only"), ("9000002", "full"), ("9000002", "main-only")])
        for record in records:   # 兩個版本的譯文和原始回應不會蓋掉對方
            self.assertTrue(os.path.exists(os.path.join(self.out_dir, record["translation"])))
        self.assertEqual(len({r["translation"] for r in records}), 4)

    def test_rerunning_sends_nothing_and_adding_a_variant_later_sends_only_the_new_runs(self):
        self.rewrite_experiment(lambda e: e["approaches"][0].pop("input_variants"))
        self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), ok_response("9000002")))
        self.rewrite_experiment(lambda e: e["approaches"][0].update(input_variants=["full", "main-only"]))
        transport = FakeTransport(main_only_response("9000001"), main_only_response("9000002"))   # 只有 main-only 要送
        self.assertEqual(self.run_cli("translate", transport=transport)[0], 0)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(len(self.read_runs()), 4)

    def test_dry_run_counts_the_requests_of_each_variant_separately(self):
        _, out, _ = self.run_cli("translate", "--dry-run", transport=FakeTransport())
        self.assertIn("請求數：4", out)
        self.assertIn("輸入版本 full：請求數 2", out)
        self.assertIn("輸入版本 main-only：請求數 2", out)

    def test_a_variant_with_nothing_left_to_do_is_still_listed_with_zero_requests(self):
        self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), main_only_response("9000001"),
                                                          ok_response("9000002"), main_only_response("9000002")))
        _, out, _ = self.run_cli("translate", "--dry-run", transport=FakeTransport())
        self.assertIn("輸入版本 full：請求數 0", out)
        self.assertIn("輸入版本 main-only：請求數 0", out)

    def test_the_report_lists_the_variants_and_the_run_count_per_variant(self):
        self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), main_only_response("9000001"),
                                                          ok_response("9000002"), main_only_response("9000002")))
        self.run_cli("check")
        self.run_cli("report")
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("- 輸入版本：full、main-only", text)
        self.assertIn("- 每篇每個 Direction 每個輸入版本的 Run 次數：1", text)

    def test_the_estimate_for_main_only_is_smaller_than_for_full(self):
        _, out, _ = self.run_cli("translate", "--dry-run", transport=FakeTransport())
        chars = {line.split("：")[0]: int(line.split("估計輸入字數 ")[1]) for line in out.splitlines() if "估計輸入字數 " in line}
        self.assertLess(chars["輸入版本 main-only"], chars["輸入版本 full"])


class ConfigTest(Workspace):
    def test_invalid_input_variants_are_rejected(self):
        for name, variants in (("empty", []), ("duplicate", ["full", "full"]), ("unknown", ["half"])):
            with self.subTest(name):
                self.rewrite_experiment(lambda e: e["approaches"][0].update(input_variants=variants))
                self.assert_fails_cleanly("input_variants")


class CheckTest(Workspace):
    input_variants = ["main-only"]

    def check_rows(self, translation):
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(translation, ensure_ascii=False))))
        code, _, err = self.run_cli("check")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.out_dir, "components.jsonl"), encoding="utf-8") as f:
            return {r["component_id"]: r for r in map(json.loads, filter(str.strip, f))}

    def test_components_that_were_not_sent_are_not_expected_back(self):
        rows = self.check_rows(main_only_translation())
        self.assertEqual(sorted(rows), ["9000001-001", "9000001-002", "title"])   # 圖說、code、tags 都不在
        self.assertEqual([r["findings"] for r in rows.values()], [[]] * 3)

    def test_a_missing_main_component_is_still_an_omission(self):
        translation = main_only_translation()
        translation["components"].pop()
        rows = self.check_rows(translation)
        self.assertEqual([f["subtype"] for f in rows["9000001-002"]["findings"]], ["omission"])

    def test_tags_the_model_invents_are_flagged_instead_of_ignored(self):
        translation = main_only_translation()
        translation["tags"] = ["Invented tag"]
        rows = self.check_rows(translation)
        self.assertEqual([f["subtype"] for f in rows["tags"]["findings"]], ["extra-component"])
        self.assertIsNone(rows["tags"]["source_type"])

    def test_a_component_that_was_never_sent_but_came_back_is_extra(self):
        translation = main_only_translation()
        translation["components"].append({"id": "9000001-003", "type": "image", "caption": "An invented caption"})
        rows = self.check_rows(translation)
        self.assertEqual([f["subtype"] for f in rows["9000001-003"]["findings"]], ["extra-component"])


class BlockIncentiveReportTest(Workspace):
    article_ids = ("9000001", "9000002", "9000003")

    def report_text(self):
        self.assertEqual(self.run_cli("check")[0], 0)
        code, _, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            return f.read()

    def run_experiment(self):
        """三篇先只跑 full（9000001、9000003 被 Block）；再只為前兩篇補跑 main-only（都沒被 Block）。"""
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked(), ok_response("9000002"), gemini_samples.prompt_blocked()))
        self.rewrite_experiment(lambda e: e["approaches"][0].update(input_variants=["full", "main-only"]))
        self.run_cli("translate", "--article", "9000001", "--article", "9000002",
                     transport=FakeTransport(main_only_response("9000001"), main_only_response("9000002")))
        return self.report_text()

    def test_block_rate_is_given_per_run_and_per_article(self):
        text = self.run_experiment()
        self.assertIn("- Block 率：按 Run 2/3（66.7%）；按文章 2/3（66.7%）；原因分佈：prompt:PROHIBITED_CONTENT 2", text)

    def test_a_blocked_article_counts_once_however_many_runs_it_has(self):
        self.rewrite_experiment(lambda e: (e["approaches"][0].update(runs=2), e["articles"].update(ids=["9000001"])))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked(), gemini_samples.prompt_blocked()))
        text = self.report_text()
        self.assertIn("- Block 率：按 Run 2/2（100.0%）；按文章 1/1（100.0%）", text)

    def test_the_variants_have_their_own_sections_and_are_never_mixed(self):
        text = self.run_experiment()
        self.assertIn("## a × trad-to-en\n", text)
        self.assertIn("## a × trad-to-en（main-only）\n", text)
        main_only_section = text.split("## a × trad-to-en（main-only）")[1].split("\n## ")[0]
        self.assertIn("- Run：計畫 3，已有紀錄 2（未完成）", main_only_section)   # 第三篇沒有補跑
        self.assertIn("- Block 率：按 Run 0/2（0.0%）；按文章 0/2（0.0%）", main_only_section)

    def test_the_block_incentive_section_compares_only_articles_that_have_both_variants(self):
        text = self.run_experiment()
        section = text.split("## Block 誘因實驗")[1].split("\n## ")[0]
        self.assertIn("比較的文章：2 篇", section)                                      # 9000003 只有 full，不在內
        self.assertIn("- full：按 Run 1/2（50.0%）；按文章 1/2（50.0%）", section)
        self.assertIn("- main-only：按 Run 0/2（0.0%）；按文章 0/2（0.0%）", section)
        self.assertIn("結果不同的文章：9000001（full 1/1、main-only 0/1）", section)
        self.assertNotIn("9000002", section.split("結果不同的文章")[1])
        self.assertNotIn("9000003", section)

    def test_an_article_is_different_when_its_block_rate_differs_not_only_when_one_side_is_never_blocked(self):
        self.rewrite_experiment(lambda e: (e["articles"].update(ids=["9000001"]),
                                           e["approaches"][0].update(runs=3, input_variants=["full", "main-only"])))
        blocked = gemini_samples.prompt_blocked
        self.run_cli("translate", transport=FakeTransport(
            blocked(), ok_response(), ok_response(),         # full：3 個 Run 被 Block 1 個
            blocked(), blocked(), blocked()))                # main-only：3 個 Run 全被 Block
        section = self.report_text().split("## Block 誘因實驗")[1].split("\n## ")[0]
        self.assertIn("結果不同的文章：9000001（full 1/3、main-only 3/3）", section)

    def test_an_article_with_the_same_block_rate_in_both_variants_is_not_different_even_with_uneven_run_counts(self):
        self.rewrite_experiment(lambda e: (e["articles"].update(ids=["9000001"]),
                                           e["approaches"][0].update(runs=2, input_variants=["full"])))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked(), ok_response()))   # full：1/2
        self.rewrite_experiment(lambda e: (e["articles"].update(ids=["9000001"]),
                                           e["approaches"][0].update(runs=2, input_variants=["full", "main-only"])))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked(), main_only_response()))   # main-only：1/2
        section = self.report_text().split("## Block 誘因實驗")[1].split("\n## ")[0]
        self.assertIn("結果不同的文章：無", section)

    def test_an_article_whose_main_only_run_only_failed_with_an_error_is_not_compared_and_the_section_says_so(self):
        self.rewrite_experiment(lambda e: (e["articles"].update(ids=["9000001"]),
                                           e["approaches"][0].update(input_variants=["full", "main-only"])))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked(), gemini_samples.unknown_format()))
        section = self.report_text().split("## Block 誘因實驗")[1].split("\n## ")[0]
        self.assertIn("比較的文章：0 篇", section)
        self.assertIn("因某個輸入版本只有 error 或 empty 的 Run 而未比較：1 篇", section)
        self.assertIn("結果不同的文章：無", section)

    def test_an_article_whose_main_only_run_only_came_back_empty_is_not_compared_either(self):
        self.rewrite_experiment(lambda e: (e["articles"].update(ids=["9000001"]),
                                           e["approaches"][0].update(input_variants=["full", "main-only"])))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked(), gemini_samples.candidate_with({"role": "model"})))
        section = self.report_text().split("## Block 誘因實驗")[1].split("\n## ")[0]
        self.assertIn("比較的文章：0 篇", section)
        self.assertIn("因某個輸入版本只有 error 或 empty 的 Run 而未比較：1 篇", section)

    def test_there_is_no_incentive_section_when_no_main_only_run_exists(self):
        self.run_cli("translate", transport=FakeTransport(ok_response("9000001"), ok_response("9000002"), ok_response("9000003")))
        self.assertNotIn("Block 誘因實驗", self.report_text())

    def test_the_section_says_so_when_no_article_has_both_variants(self):
        self.rewrite_experiment(lambda e: e["approaches"][0].update(input_variants=["main-only"]))
        self.run_cli("translate", transport=FakeTransport(*[main_only_response(a) for a in self.article_ids]))
        text = self.report_text()
        self.assertIn("沒有兩個輸入版本都有紀錄的文章", text.split("## Block 誘因實驗")[1])


if __name__ == "__main__":
    unittest.main()
