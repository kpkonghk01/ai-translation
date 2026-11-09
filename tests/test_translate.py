"""translate 子命令的測試：從 main(argv) 進入，只在 HTTP transport 注入假的。"""
import hashlib
import json
import os
import shutil
import time
import unittest
from pathlib import Path

import gemini_samples
import translation_eval
from fake_transport import FakeTransport
from fixtures import (APPROACH, FAKE_KEY, KEY_ENV, Workspace, component, good_translation, ok_response, source_article,
                      translation_with)
from translation_eval.transport import TransportForbidden, TransportTimeout


class TranslateTracerTest(Workspace):
    def test_translates_an_article_and_writes_run_record_translation_and_raw_response(self):
        sample = ok_response()
        transport = FakeTransport(sample)

        code, _, _ = self.run_cli("translate", transport=transport)

        self.assertEqual(code, 0)
        self.assertEqual(len(transport.requests), 1)
        [record] = self.read_runs()
        self.assertEqual(
            {k: record[k] for k in ("article_id", "approach", "direction", "input_variant", "run_index", "outcome")},
            {"article_id": "9000001", "approach": "a", "direction": "trad-to-en", "input_variant": "full", "run_index": 1, "outcome": "ok"},
        )
        self.assertEqual((record["input_tokens"], record["output_tokens"], record["thought_tokens"]), (120, 80, 30))
        self.assertEqual(record["model_version"], gemini_samples.MODEL_VERSION)
        with open(os.path.join(self.out_dir, record["translation"]), encoding="utf-8") as f:
            self.assertEqual(json.load(f), good_translation(source_article()))
        with open(os.path.join(self.out_dir, record["raw_response"]), "rb") as f:
            self.assertEqual(f.read(), sample.body)


class RequestTest(Workspace):
    def sent_request(self):
        transport = FakeTransport(ok_response())
        self.run_cli("translate", transport=transport)
        [request] = transport.requests
        return request

    def test_posts_to_the_model_endpoint_with_the_key_in_a_header_and_not_in_the_url(self):
        request = self.sent_request()
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent")
        self.assertEqual(request.headers["x-goog-api-key"], FAKE_KEY)

    def test_safety_settings_are_at_the_lowest_threshold_for_every_documented_category(self):
        settings = json.loads(self.sent_request().body)["safetySettings"]
        self.assertEqual({s["threshold"] for s in settings}, {"OFF"})
        self.assertEqual({s["category"] for s in settings}, {
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT", "HARM_CATEGORY_JAILBREAK"})

    def test_sends_the_article_json_without_meta_or_channel(self):
        body = json.loads(self.sent_request().body)
        article = source_article()
        self.assertEqual(json.loads(body["contents"][0]["parts"][0]["text"]),
                         {"title": article["title"], "tags": article["tags"], "components": article["components"]})

    def test_asks_for_json_output(self):
        self.assertEqual(json.loads(self.sent_request().body)["generationConfig"]["responseMimeType"], "application/json")

    def test_run_record_stores_the_id_and_hash_of_the_prompt_template(self):
        self.sent_request()
        [record] = self.read_runs()
        self.assertEqual(record["prompt_id"], "translate-trad-to-en")
        template = Path(translation_eval.__file__).parent / "prompt_templates" / f"{record['prompt_id']}.txt"
        self.assertEqual(record["prompt_hash"], hashlib.sha256(template.read_bytes()).hexdigest())
        self.assertRegex(record["config_hash"], r"^[0-9a-f]{64}$")


class ResultClassTest(Workspace):
    """每種 provider 回應都記成對應的結果類別；只有 ok 才有譯文；block 和 refusal 不重試。"""

    def test_each_response_is_recorded_as_its_result_class(self):
        partial = '{"title": "Fictional city hall'
        cases = {
            "prompt blocked": (gemini_samples.prompt_blocked("PROHIBITED_CONTENT"), "block", "prompt:PROHIBITED_CONTENT"),
            "prompt blocked other": (gemini_samples.prompt_blocked("OTHER"), "block", "prompt:OTHER"),
            "stopped by safety with partial output": (gemini_samples.safety_stopped(partial), "block", "output:SAFETY"),
            "stopped by prohibited content": (gemini_samples.safety_stopped(partial, "PROHIBITED_CONTENT"), "block", "output:PROHIBITED_CONTENT"),
            "stopped by blocklist": (gemini_samples.safety_stopped(partial, "BLOCKLIST"), "block", "output:BLOCKLIST"),
            "max tokens": (gemini_samples.max_tokens(partial), "truncated", None),
            "refusal text": (gemini_samples.refusal(), "refusal", None),
            "prose that is not a refusal": (gemini_samples.prose_not_refusal(), "malformed", None),
            "json that is not an article": (gemini_samples.ok('{"answer": 42}'), "malformed", None),
            "unknown response format": (gemini_samples.unknown_format(), "error", None),
            "unknown finish reason": (gemini_samples.safety_stopped(partial, "RECITATION"), "error", None),
            "stopped by sensitive-information filter": (gemini_samples.safety_stopped(partial, "SPII"), "block", "output:SPII"),
            "unspecified block reason and no candidates": (gemini_samples.prompt_blocked("BLOCK_REASON_UNSPECIFIED"), "error", None),
            "refusal returned as a json string": (gemini_samples.ok(json.dumps("I'm sorry, but I can't help with translating this article.")), "refusal", None),
            "refusal inside a json object": (gemini_samples.ok(json.dumps({"error": "I cannot translate this article."})), "refusal", None),
            "json array": (gemini_samples.ok('["a", "b"]'), "malformed", None),
            "stop with an empty output": (gemini_samples.candidate_with({"role": "model"}), "malformed", None),
            "stop without content": (gemini_samples.candidate_with(None), "error", None),
            "parts is null": (gemini_samples.candidate_with({"role": "model", "parts": None}), "error", None),
            "part text is not a string": (gemini_samples.candidate_with({"parts": [{"text": 5}]}), "error", None),
            "part is not an object": (gemini_samples.candidate_with({"parts": ["x"]}), "error", None),
            "http 200 with a body that is not json": (gemini_samples.not_json(), "error", None),
            "token count is not a number": (gemini_samples.with_fields(ok_response(), usageMetadata={"promptTokenCount": "many"}), "error", None),
            "model version is not a string": (gemini_samples.with_fields(ok_response(), modelVersion=["x"]), "error", None),
            "deeply nested json body": (gemini_samples.deeply_nested(), "error", None),
            "model text that is deeply nested": (gemini_samples.ok("[" * 200000), "malformed", None),
            "prompt blocked with a malformed usageMetadata": (gemini_samples.with_fields(gemini_samples.prompt_blocked(), usageMetadata=[]), "block", "prompt:PROHIBITED_CONTENT"),
            "prompt blocked with a bad model version": (gemini_samples.with_fields(gemini_samples.prompt_blocked(), modelVersion=5), "block", "prompt:PROHIBITED_CONTENT"),
            "safety stop with a malformed usageMetadata": (gemini_samples.with_fields(gemini_samples.safety_stopped(partial), usageMetadata="x"), "block", "output:SAFETY"),
            "max tokens with a malformed usageMetadata": (gemini_samples.with_fields(gemini_samples.max_tokens(partial), usageMetadata=[]), "truncated", None),
            "translated text is a number": (gemini_samples.ok(json.dumps(
                translation_with(lambda t: component(t, "002").update(text=123)))), "malformed", None),
            "translated caption is a list": (gemini_samples.ok(json.dumps(
                translation_with(lambda t: component(t, "003").update(caption=["x"])))), "malformed", None),
        }
        for name, (sample, outcome, reason) in cases.items():
            with self.subTest(name):
                shutil.rmtree(self.data_dir, ignore_errors=True)
                transport = FakeTransport(sample)

                code, _, _ = self.run_cli("translate", transport=transport)

                self.assertEqual(code, 0)
                self.assertEqual(len(transport.requests), 1, "block、refusal 等結果不重試")
                [record] = self.read_runs()
                self.assertEqual(record["outcome"], outcome)
                if reason:
                    self.assertEqual(record["reason"], reason)
                self.assertIsNone(record["translation"], "只有 ok 的 Run 才有譯文，殘缺的輸出不能留作譯文")
                with open(os.path.join(self.out_dir, record["raw_response"]), "rb") as f:
                    self.assertEqual(f.read(), sample.body)

    def test_json_wrapped_in_one_outer_code_fence_is_accepted(self):
        fenced = "```json\n" + json.dumps(good_translation(source_article()), ensure_ascii=False) + "\n```"
        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(fenced)))
        self.assertEqual(self.read_runs()[0]["outcome"], "ok")

    def test_any_other_deviation_from_plain_json_is_malformed(self):
        text = json.dumps(good_translation(source_article()), ensure_ascii=False)
        for name, wrapped in (("prose before the json", "Here you go:\n" + text), ("prose after the fence", "```json\n" + text + "\n```\nDone.")):
            with self.subTest(name):
                shutil.rmtree(self.data_dir, ignore_errors=True)
                self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(wrapped)))
                self.assertEqual(self.read_runs()[0]["outcome"], "malformed")


class RetryTest(Workspace):
    """429、5xx、timeout 以退避方式重試，每次嘗試都記錄；max_attempts 是 3（見 fixtures.APPROACH）。"""

    def translate(self, *scripted):
        transport = FakeTransport(*scripted)
        code, _, _ = self.run_cli("translate", transport=transport)
        self.assertEqual(code, 0)
        [record] = self.read_runs()
        return transport, record

    def test_retries_a_429_and_a_5xx_then_records_the_successful_run_with_all_attempts(self):
        transport, record = self.translate(gemini_samples.rate_limited(), gemini_samples.server_error(503), ok_response())
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(record["outcome"], "ok")
        self.assertEqual([a["http_status"] for a in record["attempts"]], [429, 503, 200])
        self.assertEqual([a["outcome"] for a in record["attempts"]], ["error", "error", "ok"])

    def test_retries_a_timeout(self):
        transport, record = self.translate(TransportTimeout("timeout"), ok_response())
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(record["outcome"], "ok")
        self.assertEqual([a["http_status"] for a in record["attempts"]], [None, 200])
        self.assertEqual(record["attempts"][0]["reason"], "timeout")

    def test_gives_up_after_max_attempts_and_records_error(self):
        transport, record = self.translate(*[gemini_samples.rate_limited()] * 3)
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(record["outcome"], "error")
        self.assertEqual(record["reason"], "HTTP 429")
        self.assertEqual(len(record["attempts"]), 3)
        self.assertIsNone(record["translation"])

    def test_does_not_retry_a_client_error(self):
        transport, record = self.translate(gemini_samples.client_error(400))
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual((record["outcome"], record["reason"]), ("error", "HTTP 400 INVALID_ARGUMENT"))

    def test_waits_longer_before_each_further_attempt(self):
        self.write_json(self.approach_path, {**APPROACH, "retry": {"max_attempts": 3, "base_delay_seconds": 0.05}})
        started = time.monotonic()
        self.translate(gemini_samples.rate_limited(), gemini_samples.rate_limited(), ok_response())
        self.assertGreaterEqual(time.monotonic() - started, 0.05 + 0.10)   # 兩次重試前各等 0.05、0.10 秒


class RunPlanTest(Workspace):
    """Run 的 key 是 文章 × Approach × Direction × 輸入版本 × Run 序號；重跑只補沒有紀錄的 Run。"""

    article_ids = ("9000001", "9000002")
    runs = 2

    def keys(self):
        return sorted((r["article_id"], r["run_index"]) for r in self.read_runs())

    def translate(self, n_responses, *extra):
        transport = FakeTransport(*[ok_response()] * n_responses)
        code, out, err = self.run_cli("translate", *extra, transport=transport)
        return code, out, err, transport

    def test_runs_every_article_as_many_times_as_configured(self):
        code, _, _, transport = self.translate(4)
        self.assertEqual(code, 0)
        self.assertEqual(len(transport.requests), 4)
        self.assertEqual(self.keys(), [("9000001", 1), ("9000001", 2), ("9000002", 1), ("9000002", 2)])

    def test_rerunning_only_fills_in_the_runs_that_have_no_record(self):
        self.translate(1, "--limit", "1")
        self.assertEqual(self.keys(), [("9000001", 1)])
        _, _, _, transport = self.translate(3)   # 只有 3 個回應：多送一個請求就會令測試失敗
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(self.keys(), [("9000001", 1), ("9000001", 2), ("9000002", 1), ("9000002", 2)])

    def test_rerunning_a_finished_experiment_sends_nothing(self):
        self.translate(4)
        code, _, _, transport = self.translate(0)
        self.assertEqual(code, 0)
        self.assertEqual(transport.requests, [])
        self.assertEqual(len(self.read_runs()), 4)

    def test_error_runs_are_redone_only_when_asked_and_replace_the_old_record(self):
        self.rewrite_experiment(lambda e: e["approaches"][0].update(runs=1))
        failing = FakeTransport(gemini_samples.unknown_format(), gemini_samples.unknown_format())
        self.run_cli("translate", transport=failing)
        self.assertEqual([r["outcome"] for r in self.read_runs()], ["error", "error"])

        _, _, _, transport = self.translate(0)
        self.assertEqual(transport.requests, [], "沒有指定參數就不重做 error 的 Run")

        _, _, _, transport = self.translate(2, "--redo-errors")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual([r["outcome"] for r in self.read_runs()], ["ok", "ok"], "舊的 error 紀錄被取代，不是並存")

    def test_redo_errors_does_not_redo_block_or_refusal(self):
        self.rewrite_experiment(lambda e: e["articles"].update(ids=["9000001"]))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked(), gemini_samples.refusal()))
        _, _, _, transport = self.translate(0, "--redo-errors")
        self.assertEqual(transport.requests, [])
        self.assertEqual([r["outcome"] for r in self.read_runs()], ["block", "refusal"])

    def test_redo_errors_keeps_each_old_record_until_its_replacement_exists(self):
        self.rewrite_experiment(lambda e: e["articles"].update(ids=["9000001"]))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.unknown_format(), gemini_samples.unknown_format()))

        # 第一個重做成功，第二個被 transport 拒絕：已成功的換掉舊紀錄，還沒重做的舊紀錄不能不見
        refusing = FakeTransport(ok_response(), TransportForbidden("test"))
        code, _, _ = self.run_cli("translate", "--redo-errors", transport=refusing)

        self.assertNotEqual(code, 0)
        self.assertEqual(sorted(r["outcome"] for r in self.read_runs()), ["error", "ok"])

    def test_redo_errors_with_a_transport_that_refuses_loses_nothing(self):
        self.rewrite_experiment(lambda e: e["articles"].update(ids=["9000001"]))
        self.run_cli("translate", transport=FakeTransport(gemini_samples.unknown_format(), gemini_samples.unknown_format()))
        code, _, _ = self.run_cli("translate", "--redo-errors")   # 沒有注入 transport，離線旗標開著
        self.assertNotEqual(code, 0)
        self.assertEqual([r["outcome"] for r in self.read_runs()], ["error", "error"])

    def test_article_option_limits_the_run_to_the_given_articles(self):
        _, _, _, transport = self.translate(2, "--article", "9000002")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual({a for a, _ in self.keys()}, {"9000002"})

    def test_article_not_in_the_experiment_fails_without_writing_anything(self):
        code, _, err, transport = self.translate(0, "--article", "9000003")
        self.assertNotEqual(code, 0)
        self.assertIn("9000003", err)
        self.assertEqual(transport.requests, [])
        self.assertFalse(os.path.exists(self.data_dir))

    def test_limit_caps_the_number_of_runs_started(self):
        _, _, _, transport = self.translate(3, "--limit", "3")
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(len(self.read_runs()), 3)


class DryRunTest(Workspace):
    article_ids = ("9000001", "9000002")
    runs = 2

    def test_dry_run_sends_nothing_writes_nothing_and_needs_no_key(self):
        del os.environ[KEY_ENV]
        transport = FakeTransport()   # 任何請求都會令測試失敗

        code, out, _ = self.run_cli("translate", "--dry-run", transport=transport)

        self.assertEqual(code, 0)
        self.assertEqual(transport.requests, [])
        self.assertFalse(os.path.exists(self.data_dir))
        self.assertIn("請求數：4", out)
        self.assertIn("https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent", out)
        self.assertIn("併發：1", out)
        self.assertNotIn(FAKE_KEY, out)

    def test_estimated_input_chars_match_the_requests_a_real_run_sends(self):
        real = FakeTransport(*[ok_response()] * 4)
        self.run_cli("translate", transport=real)
        sent_chars = 0
        for request in real.requests:
            body = json.loads(request.body)
            sent_chars += len(body["systemInstruction"]["parts"][0]["text"]) + len(body["contents"][0]["parts"][0]["text"])
        shutil.rmtree(self.data_dir)

        _, out, _ = self.run_cli("translate", "--dry-run", transport=FakeTransport())

        self.assertIn(f"估計輸入字數：{sent_chars}", out)

    def test_dry_run_counts_only_the_runs_that_are_still_to_do(self):
        self.run_cli("translate", "--limit", "1", transport=FakeTransport(ok_response()))
        _, out, _ = self.run_cli("translate", "--dry-run", "--article", "9000001", transport=FakeTransport())
        self.assertIn("請求數：1", out)
        self.assertIn("已有紀錄而略過：1", out)


if __name__ == "__main__":
    unittest.main()
