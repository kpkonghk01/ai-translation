"""report 子命令的測試：Block／Refusal 報告。從 main(argv) 進入，transport 注入假的。"""
import hashlib
import json
import os
import unittest
from pathlib import Path

import gemini_samples
import translation_eval
from fake_transport import FakeTransport
from fixtures import ARTICLE_ID, Workspace, component, good_translation, source_article, translation_with


class ReportTest(Workspace):
    runs = 5

    def report_text(self):
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            return f.read()

    def run_pipeline(self, *samples):
        transport = FakeTransport(*samples)
        self.assertEqual(self.run_cli("translate", transport=transport)[0], 0)
        self.assertEqual(self.run_cli("check")[0], 0)
        code, out, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        return transport

    def mixed_runs(self):
        missing = translation_with(lambda t: t["components"].remove(component(t, "002")))
        return self.run_pipeline(
            gemini_samples.ok(json.dumps(good_translation(source_article()))),
            gemini_samples.ok(json.dumps(missing)),
            gemini_samples.prompt_blocked("PROHIBITED_CONTENT"),
            gemini_samples.safety_stopped('{"title": "Fict', "SAFETY"),
            gemini_samples.refusal(),
        )

    def test_section_per_approach_and_direction_lists_every_rate_with_numerator_and_denominator(self):
        self.mixed_runs()
        text = self.report_text()
        self.assertIn("## a × trad-to-en", text)
        self.assertIn("- Run：計畫 5，已有紀錄 5", text)
        self.assertIn("- Block 率：2/5（40.0%）；原因分佈：output:SAFETY 1、prompt:PROHIBITED_CONTENT 1", text)
        self.assertIn("- Refusal 率：1/5（20.0%）", text)
        self.assertIn("- 其他失敗：malformed 0/5（0.0%）、truncated 0/5（0.0%）、error 0/5（0.0%）", text)
        self.assertIn("- 漏段數：1（已檢查 2 個 ok 的 Run、共 12 個 Component）", text)

    def test_other_failures_are_counted_apart_from_block_and_refusal(self):
        self.run_pipeline(
            gemini_samples.prose_not_refusal(), gemini_samples.max_tokens('{"title": "Fict'), gemini_samples.unknown_format(),
            gemini_samples.client_error(400), gemini_samples.ok(json.dumps(good_translation(source_article()))),
        )
        text = self.report_text()
        self.assertIn("- Block 率：0/5（0.0%）；原因分佈：無", text)
        self.assertIn("- Refusal 率：0/5（0.0%）", text)
        self.assertIn("- 其他失敗：malformed 1/5（20.0%）、truncated 1/5（20.0%）、error 2/5（40.0%）", text)

    def test_header_lists_the_experiment_the_approach_the_prompt_hash_and_the_model_versions_actually_returned(self):
        self.mixed_runs()
        template = Path(translation_eval.__file__).parent / "prompt_templates" / "translate-trad-to-en.v2.txt"
        text = self.report_text()
        self.assertIn("# 評核報告：tracer", text)
        self.assertIn("- Direction：trad-to-en", text)
        self.assertIn("- 模型家族：gemini；provider：gemini；模型：gemini-3.1-flash-lite", text)
        self.assertIn("- 每篇每個 Direction 的 Run 次數：5", text)
        self.assertIn(f"- translate-trad-to-en.v2：{hashlib.sha256(template.read_bytes()).hexdigest()}（5 個 Run）", text)
        self.assertIn(f"- 實際回應的模型版本：{gemini_samples.MODEL_VERSION}（5 個 Run）", text)
        self.assertRegex(text, r"- 設定 hash：[0-9a-f]{64}（5 個 Run）")

    def test_runs_without_a_record_are_shown_as_incomplete(self):
        self.runs = 5
        self.assertEqual(self.run_cli("translate", "--limit", "2", transport=FakeTransport(*[gemini_samples.refusal()] * 2))[0], 0)
        self.run_cli("check")
        self.assertEqual(self.run_cli("report")[0], 0)
        text = self.report_text()
        self.assertIn("- Run：計畫 5，已有紀錄 2（未完成）", text)
        self.assertIn("- Refusal 率：2/2（100.0%）", text)

    def test_fails_without_a_report_when_an_ok_run_has_not_been_checked(self):
        self.run_cli("translate", "--limit", "1", transport=FakeTransport(gemini_samples.ok(json.dumps(good_translation(source_article())))))
        code, _, err = self.run_cli("report")
        self.assertNotEqual(code, 0)
        self.assertIn("check", err)
        self.assertIn(ARTICLE_ID, err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "report.md")))

    def test_fails_when_there_are_no_runs(self):
        code, _, err = self.run_cli("report")
        self.assertNotEqual(code, 0)
        self.assertIn("translate", err)
        self.assertFalse(os.path.exists(self.data_dir))


if __name__ == "__main__":
    unittest.main()
