"""摘要版報告：只含數字、文章 id 和 Component id。測試在輸入中放進虛構的原文、譯文、事實文字、Judge 的理由、審閱備註和 provider 回的字串，
確認它們都沒有出現在摘要版；對照組是審閱清單（review.md，含全部內容）和完整版（Source defect 與同家族例外的理由），證明這些字串真的存在、找得到。
也確認報告開頭有重現結果需要的資訊。從 main(argv) 進入。只用虛構資料。"""
import json
import os
import re
import shutil

import gemini_samples
from fake_transport import FakeTransport
from fixtures import good_simplified_translation, ok_response
from gate_helpers import S1_FACT, S2_FACT, WEAK, ReviewCase
from judge_helpers import fact_output, finding, screen_item

SOURCE_TEXTS = ["虛構市政廳翻新工程延誤", "翻新工程延誤半年，預算超支", "市民陳虛構表示，工程拖延令他十分不滿", "施工中的虛構市政廳"]
TRANSLATION_TEXTS = ["Fictional city hall renovation delayed", "The renovation is six months late and over budget", "Resident Chan Fictional said",
                     "The fictional city hall under construction"]
FACT_TEXTS = [S1_FACT, "預算超支", S2_FACT]
JUDGE_REASON = "虛構的理由：這句話被削弱了"
DEFECT = {"source_span": "延誤半年", "issue": "虛構的原文瑕疵：這裡有個筆誤"}
OTHER_DEFECT = {"source_span": "預算超支", "issue": "虛構的另一個瑕疵"}
EXCEPTION_REASON = "虛構的同家族例外理由"
NOTE = "虛構的審閱備註：已跟編輯確認"


class SummaryCase(ReviewCase):
    def setUp(self):
        super().setUp()
        self.use_judging(screening=True, same_family_exceptions=[{"approach": "a", "judge": "j1", "reason": EXCEPTION_REASON}])

    def busy_world(self):
        """Judge 提出 Finding、Source defect、Gate 失敗、uncertain、篩查的條目；Pan 另外寫了審閱決定。"""
        mqm_finding = {"findings": [finding(severity="major", reason=JUDGE_REASON)], "source_defects": [DEFECT, OTHER_DEFECT]}
        self.run_judges(
            mqm={("9000001-001", 1): mqm_finding},
            fact={"9000001-001": lambda p: fact_output(p, {S1_FACT: WEAK | {"reason": JUDGE_REASON}}),
                  ("9000001-002", 2): lambda p: fact_output(p, {S2_FACT: WEAK})},
            screen={1: {"items": [screen_item(reason=JUDGE_REASON)]}, 2: {"items": [screen_item(reason=JUDGE_REASON)]}})
        item = self.item(self.review_text(), "事實（detail）：工程延誤半年")
        self.decide(item, "accept", note=NOTE)
        self.review_text()   # 審閱清單帶著決定重寫一次
        code, _, err = self.run_cli("report")
        self.assertEqual(code, 0, err)


class ContentTest(SummaryCase):
    def test_no_article_translation_fact_or_judge_text_is_in_the_summary_but_the_full_report_has_the_judge_and_defect_text(self):
        self.busy_world()
        summary, full = self.output_text("summary.md"), self.output_text("report.md")
        for text in [*SOURCE_TEXTS, *TRANSLATION_TEXTS, *FACT_TEXTS, JUDGE_REASON, DEFECT["issue"], DEFECT["source_span"], EXCEPTION_REASON, NOTE, "虛構審閱者"]:
            self.assertNotIn(text, summary, text)
        # 完整版有的（證明上面的比對找得到它們）：Judge 寫的 Source defect 和同家族的例外理由
        for text in (DEFECT["issue"], DEFECT["source_span"], EXCEPTION_REASON):
            self.assertIn(text, full, text)
        review = self.output_text("review.md")   # 對照組：審閱清單有原文、譯文、事實、Judge 的理由和審閱備註
        for text in [SOURCE_TEXTS[1], TRANSLATION_TEXTS[1], S1_FACT, JUDGE_REASON, NOTE]:
            self.assertIn(text, review, text)

    def test_the_summary_keeps_the_numbers_article_ids_and_component_ids(self):
        self.busy_world()
        summary = self.output_text("summary.md")
        self.assertIn("# 評核報告摘要：tracer", summary)
        self.assertIn("淡化率：1/1（100.0%）", summary)   # Gate：句 2 還有 uncertain 的事實，沒有結果，不在分母
        self.assertRegex(summary, r"- Main：總罰分 0（只計一致的 Finding）／5（連 uncertain 一起計）；每千字原文的罰分 0\.0／113\.6（原文 44 字）")   # 分數
        self.assertIn("Block 率：按 Run 0/1（0.0%）", summary)
        self.assertIn("- 文章 9000001 ・ 9000001-001：2 處（Judge j1）", summary)   # Source defect：只有位置與數量，同一個 Component 的合成一行
        self.assertIn("同家族的例外：a × j1\n", summary)   # 沒有理由
        self.assertIn("## 審閱\n\n- 必看 ", summary)

    def test_summary_and_full_report_are_written_together_from_the_same_numbers(self):
        self.busy_world()
        gate = lambda text: re.search(r"淡化率：[^\n]*", text).group(0)   # noqa: E731
        self.assertEqual(gate(self.output_text("summary.md")), gate(self.output_text("report.md")))
        self.assertTrue(os.path.exists(os.path.join(self.out_dir, "summary.md")))

    def test_strings_the_provider_wrote_are_replaced_unless_they_are_plain_codes(self):
        shutil.rmtree(self.data_dir)
        self.runs = 2
        self.write_json(self.config_path, self.experiment())
        weird_version = gemini_samples.with_fields(ok_response(), modelVersion="虛構的模型版本")
        blocked = gemini_samples.prompt_blocked("虛構的 原因")
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(weird_version, blocked))[0], 0)
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        full, summary = self.output_text("report.md"), self.output_text("summary.md")
        for text in ("虛構的模型版本", "虛構的 原因"):
            self.assertIn(text, full)
            self.assertNotIn(text, summary)
        self.assertIn("實際回應的模型版本：gemini-3.1-flash-lite-fake（1 個 Run）、（其他）（1 個 Run）", summary)
        self.assertIn("原因分佈：（其他） 1", summary)

    def test_an_english_phrase_or_a_code_that_is_too_long_is_not_passed_through_but_a_code_of_sixty_characters_is(self):
        shutil.rmtree(self.data_dir)
        self.runs = 3
        self.write_json(self.config_path, self.experiment())
        versions = ["The renovation is six months late", "x" * 61, "v" + "1" * 59]
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(*[gemini_samples.with_fields(ok_response(), modelVersion=v) for v in versions]))[0], 0)
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        summary = self.output_text("summary.md")
        self.assertIn("實際回應的模型版本：v" + "1" * 59 + "（1 個 Run）、（其他）（2 個 Run）", summary)
        self.assertNotIn("The renovation", summary)
        self.assertIn("The renovation is six months late（1 個 Run）", self.output_text("report.md"))

    def test_a_plain_block_code_is_kept_in_the_summary(self):
        shutil.rmtree(self.data_dir)
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked("PROHIBITED_CONTENT")))[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        self.assertIn("prompt:PROHIBITED_CONTENT 1", self.output_text("summary.md"))


class HeaderTest(SummaryCase):
    def test_the_report_opens_with_everything_needed_to_reproduce_the_result(self):
        self.busy_world()
        full = self.output_text("report.md")
        head = full.split("\n## a × trad-to-en")[0]
        for needed in ("- 實驗 id：tracer", "ground truth：draft；hash ", "## Judge 設定", "- j1（primary）：家族 claude；provider gemini；模型 gemini-3.1-pro；prompt judge-mqm ",
                       "j1 的Must-retain fact 核對：prompt judge-facts ", "j1 的全文篩查：prompt judge-screen ", "- 權重：minor 1、major 5、critical 25",
                       "- 正規化：每千字原文的罰分", "- 同家族的例外：a × j1：", "## 審閱\n\n- 必看 ", "- 實際回應的模型版本：gemini-3.1-flash-lite-fake", "## Prompt",
                       "translate-trad-to-en："):
            self.assertIn(needed, head)

    def test_the_baseline_label_is_listed_in_the_header(self):
        shutil.rmtree(self.data_dir)
        directory = os.path.join(self.tmp, "baseline")
        self.write_baseline(directory, "9000001", good_simplified_translation("9000001"), label="fictional-service-1")
        self.extra_experiment["baselines"] = [{"id": "e", "dir": directory, "direction": "trad-to-simplified"}]
        self.extra_experiment["directions"] = ["trad-to-en", "trad-to-simplified"]
        self.write_json(self.config_path, self.experiment())
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(ok_response(), gemini_samples.ok(json.dumps(good_simplified_translation("9000001"), ensure_ascii=False))))[0], 0)
        self.assertEqual(self.run_cli("import-baseline")[0], 0)
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        for which in ("report.md", "summary.md"):
            self.assertIn("## Baseline\n\n- e：fictional-service-1（1 個 Run）", self.output_text(which))
