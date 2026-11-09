"""OpenRouter（方案 F 等）：每個請求都帶資料保護欄位，缺任何一項就拒絕送出；實際服務的 provider 不在允許清單內記為 error；
內容審查記為 Block。從 main(argv) 進入。"""
import json
import os
import shutil
import unittest
from unittest import mock

import openrouter_samples as samples
from fake_transport import FakeTransport
from fixtures import FAKE_KEY, Workspace, good_translation, source_article

KEY_ENV = "OPENROUTER_API_KEY"
URL = "https://openrouter.ai/api/v1/chat/completions"
ROUTING = {"only": ["deepinfra", "novita"], "confirmed_on": "2026-11-09"}
APPROACH = {"id": "f", "family": "hunyuan", "provider": "openrouter", "model": "tencent/hy-mt2", "api_key_env": KEY_ENV,
            "timeout_seconds": 30, "retry": {"max_attempts": 3, "base_delay_seconds": 0}, "routing": ROUTING}


def ok_response(provider="DeepInfra"):
    return samples.ok(json.dumps(good_translation(source_article()), ensure_ascii=False), provider)


class OpenRouterCase(Workspace):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {KEY_ENV: FAKE_KEY})
        env.start()
        self.addCleanup(env.stop)
        self.use_approach(APPROACH)

    def use_approach(self, approach):
        self.write_json(self.approach_path, approach)

    def translate(self, *scripted, args=()):
        shutil.rmtree(self.data_dir, ignore_errors=True)   # 同一個測試會呼叫好幾次；有紀錄的 Run 會被略過
        transport = FakeTransport(*scripted)
        code, out, err = self.run_cli("translate", *args, transport=transport)
        return code, transport, out, err


class RequestTest(OpenRouterCase):
    def test_every_request_carries_all_four_protection_fields_exactly(self):
        # 429 之後重試：重試的請求也要有，所以兩個請求都檢查
        code, transport, _, err = self.translate(samples.error(429), ok_response())
        self.assertEqual((code, len(transport.requests)), (0, 2), err)
        for request in transport.requests:
            self.assertEqual(json.loads(request.body)["provider"],
                             {"zdr": True, "data_collection": "deny", "only": ["deepinfra", "novita"], "allow_fallbacks": False})

    def test_posts_to_openrouter_with_the_key_as_a_bearer_token_and_not_in_the_url(self):
        _, transport, _, _ = self.translate(ok_response())
        request = transport.requests[0]
        self.assertEqual((request.method, request.url), ("POST", URL))
        self.assertEqual(request.headers["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, request.url)

    def test_the_body_has_the_model_messages_and_json_output(self):
        _, transport, _, _ = self.translate(ok_response())
        body = json.loads(transport.requests[0].body)
        self.assertEqual(body["model"], "tencent/hy-mt2")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual(body["response_format"], {"type": "json_object"})


class RoutingRequiredTest(OpenRouterCase):
    def assert_refuses(self, approach):
        """真跑被拒絕：非 0 結束，沒有送出請求，沒有寫出任何檔案。（--dry-run 只顯示，不拒絕，另有測試）"""
        self.use_approach(approach)
        shutil.rmtree(self.data_dir, ignore_errors=True)
        transport = FakeTransport()
        code, out, err = self.run_cli("translate", transport=transport)
        self.assertNotEqual(code, 0)
        self.assertIn("允許清單", err)
        self.assertEqual((out, transport.requests), ("", []))
        self.assertFalse(os.path.exists(self.data_dir))

    def test_no_request_is_built_without_a_confirmed_allowlist(self):
        base = {k: v for k, v in APPROACH.items() if k != "routing"}
        for name, approach in (
            ("no routing at all", base),
            ("empty allowlist", {**base, "routing": {"only": [], "confirmed_on": "2026-11-09"}}),
            ("allowlist not confirmed", {**base, "routing": {"only": ["deepinfra"]}}),
            ("confirmation date without a list", {**base, "routing": {"confirmed_on": "2026-11-09"}}),
        ):
            with self.subTest(name):
                self.assert_refuses(approach)

    def test_a_dry_run_still_shows_the_routing_and_says_when_it_would_be_refused(self):
        self.use_approach({k: v for k, v in APPROACH.items() if k != "routing"})
        code, out, _ = self.run_cli("translate", "--dry-run", transport=FakeTransport())
        self.assertEqual(code, 0)
        self.assertIn("尚未確認", out)
        self.use_approach(APPROACH)
        shutil.rmtree(self.data_dir, ignore_errors=True)
        _, out, _ = self.run_cli("translate", "--dry-run", transport=FakeTransport())
        self.assertIn(URL, out)
        for part in ("zdr", "data_collection=deny", "allow_fallbacks=false", "deepinfra", "novita", "2026-11-09"):
            self.assertIn(part, out)


class ConfigTest(OpenRouterCase):
    def test_invalid_settings_are_rejected(self):
        for name, approach, needle in (
            ("routing on another provider", {"id": "a", "family": "gemini", "provider": "gemini", "model": "gemini-3.1-flash-lite",
                                             "api_key_env": "GEMINI_API_KEY", "routing": ROUTING}, "routing"),
            ("another provider's key variable", {**APPROACH, "api_key_env": "GEMINI_API_KEY"}, "OPENROUTER_API_KEY"),
            ("slug with upper case", {**APPROACH, "routing": {**ROUTING, "only": ["DeepInfra"]}}, "only"),
            ("slug with a space", {**APPROACH, "routing": {**ROUTING, "only": ["deep infra"]}}, "only"),
            ("duplicate slug", {**APPROACH, "routing": {**ROUTING, "only": ["deepinfra", "deepinfra"]}}, "only"),
            ("not a date", {**APPROACH, "routing": {**ROUTING, "confirmed_on": "yesterday"}}, "confirmed_on"),
            ("unknown routing field", {**APPROACH, "routing": {**ROUTING, "fallbacks": True}}, "fallbacks"),
            ("a slash in a gemini model", {"id": "a", "family": "gemini", "provider": "gemini", "model": "google/gemini",
                                           "api_key_env": "GEMINI_API_KEY"}, "model"),
        ):
            with self.subTest(name):
                self.use_approach(approach)
                self.assert_fails_cleanly(needle)

    def test_a_variant_slug_is_accepted(self):
        self.use_approach({**APPROACH, "routing": {**ROUTING, "only": ["deepinfra/turbo", "google-vertex/us-east5"]}})
        code, _, _, err = self.translate(ok_response("DeepInfra"))
        self.assertEqual(code, 0, err)


class ServedProviderTest(OpenRouterCase):
    def test_the_provider_that_served_the_request_is_recorded(self):
        self.translate(ok_response("DeepInfra"))
        [record] = self.read_runs()
        self.assertEqual((record["outcome"], record["served_provider"]), ("ok", "DeepInfra"))

    def test_display_names_match_slugs_ignoring_case_and_punctuation(self):
        self.use_approach({**APPROACH, "routing": {**ROUTING, "only": ["google-vertex/us-east5", "deepinfra"]}})
        for served in ("Google Vertex", "google-vertex", "DEEPINFRA", "DeepInfra"):
            with self.subTest(served):
                self.translate(ok_response(served))
                self.assertEqual(self.read_runs()[0]["outcome"], "ok")

    def test_a_provider_outside_the_allowlist_is_an_error_and_its_output_is_not_kept_as_a_translation(self):
        _, _, _, _ = self.translate(ok_response("SomewhereElse"))
        [record] = self.read_runs()
        self.assertEqual((record["outcome"], record["reason"], record["served_provider"]), ("error", "provider 不在允許清單內", "SomewhereElse"))
        self.assertIsNone(record["translation"])
        self.assertIsNotNone(record["raw_response"])

    def test_a_response_that_does_not_say_which_provider_served_it_is_an_error(self):
        self.translate(ok_response(None))
        [record] = self.read_runs()
        self.assertEqual((record["outcome"], record["reason"]), ("error", "回應沒有寫是哪個 provider 服務的"))

    def test_a_blocked_response_from_an_unlisted_provider_is_still_flagged_as_an_error(self):
        self.translate(samples.finished('{"title": "Fict', "content_filter", provider="SomewhereElse"))
        self.assertEqual(self.read_runs()[0]["outcome"], "error")

    def test_the_report_lists_the_providers_that_served_the_runs(self):
        self.translate(ok_response("DeepInfra"))
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            self.assertIn("- 實際服務的 provider：DeepInfra（1 個 Run）", f.read())


class ResultClassTest(OpenRouterCase):
    def test_each_response_is_recorded_as_its_result_class(self):
        partial = '{"title": "Fictional'
        cases = {
            "ok": (ok_response(), "ok", None),
            "moderation 403": (samples.moderation(), "block", "moderation:sexual"),
            "moderation with two reasons": (samples.moderation(reasons=("hate", "violence")), "block", "moderation:hate,violence"),
            "moderation sent with status 200": (samples.error_with_200(403, {"reasons": ["sexual"]}), "block", "moderation:sexual"),
            "content filter finish": (samples.finished(partial, "content_filter"), "block", "output:content_filter"),
            "guardrail or permission 403": (samples.error(403), "error", "HTTP 403"),
            "insufficient credits": (samples.error(402), "error", "HTTP 402"),
            "invalid key": (samples.error(401), "error", "HTTP 401"),
            "no provider satisfies only": (samples.error(404), "error", "HTTP 404"),
            "bad request": (samples.error(400), "error", "HTTP 400"),
            "length": (samples.finished(partial, "length"), "truncated", None),
            "finish reason error": (samples.choice_error(), "error", None),
            "error object with status 200": (samples.error_with_200(400), "error", "HTTP 400"),
            "refusal field": (samples.provider_refusal(), "refusal", None),
            "refusal wording": (samples.ok("I'm sorry, but I can't help with translating this article."), "refusal", None),
            "prose that is not a refusal": (samples.ok("Here is the translation of the article."), "malformed", None),
            "json that is not an article": (samples.ok('{"answer": 42}'), "malformed", None),
            "unknown format": (samples.unknown_format(), "error", None),
            "html with 200": (samples.not_json(), "error", None),
            "html with 400": (samples.not_json(400), "error", "HTTP 400"),
            "deeply nested json": (samples.deeply_nested(), "error", None),
            "no finish reason": (samples.finished("{}", None), "error", None),
            "token count is not a number": (samples.with_fields(ok_response(), usage={"prompt_tokens": "many"}), "error", None),
        }
        for name, (sample, outcome, reason) in cases.items():
            with self.subTest(name):
                code, transport, _, err = self.translate(sample)
                self.assertEqual(code, 0, err)
                self.assertEqual(len(transport.requests), 1, "block、refusal 等結果不重試")
                [record] = self.read_runs()
                self.assertEqual(record["outcome"], outcome)
                if reason:
                    self.assertEqual(record["reason"], reason)
                self.assertEqual(record["translation"] is not None, outcome == "ok")
                with open(os.path.join(self.out_dir, record["raw_response"]), "rb") as f:
                    self.assertEqual(f.read(), sample.body)

    def test_a_filtered_response_is_a_block_even_when_the_rest_of_it_is_malformed(self):
        for name, fields in (("usage is a list", {"usage": []}), ("model is a number", {"model": 5}), ("a float token count", {"usage": {"prompt_tokens": 1.5}})):
            with self.subTest(name):
                self.translate(samples.with_fields(samples.finished("{}", "content_filter"), **fields))
                self.assertEqual(self.read_runs()[0]["outcome"], "block")

    def test_tokens_and_the_model_version_come_from_the_response(self):
        self.translate(ok_response())
        [record] = self.read_runs()
        self.assertEqual((record["input_tokens"], record["output_tokens"], record["thought_tokens"]), (120, 80, 30))
        self.assertEqual((record["model_version"], record["provider"]), (samples.MODEL_VERSION, "openrouter"))

    def test_retries_throttling_timeouts_and_unavailable_routing_but_not_a_moderation_block(self):
        code, transport, _, err = self.translate(samples.error(429), samples.error(408), samples.error(503), args=())
        self.assertEqual(len(transport.requests), 3, err)   # 三次都失敗，用盡嘗試次數
        self.assertEqual(self.read_runs()[0]["outcome"], "error")
        self.assertEqual([a["http_status"] for a in self.read_runs()[0]["attempts"]], [429, 408, 503])
        code, transport, _, _ = self.translate(samples.moderation())
        self.assertEqual(len(transport.requests), 1)

    def test_a_retryable_error_inside_a_200_is_retried(self):
        _, transport, _, err = self.translate(samples.error_with_200(429), ok_response())
        self.assertEqual(len(transport.requests), 2, err)
        self.assertEqual(self.read_runs()[0]["outcome"], "ok")


class SafetyTest(OpenRouterCase):
    def test_a_missing_key_stops_before_any_request_and_names_only_the_variable(self):
        del os.environ[KEY_ENV]
        code, transport, _, err = self.translate()
        self.assertNotEqual(code, 0)
        self.assertIn(KEY_ENV, err)
        self.assertEqual(transport.requests, [])

    def test_the_key_never_reaches_an_output_file_even_when_the_server_echoes_it(self):
        self.translate(samples.with_fields(ok_response(), echo=f"you sent {FAKE_KEY}", provider=f"DeepInfra"))
        self.assertNotIn(FAKE_KEY, self.all_output_text())


if __name__ == "__main__":
    unittest.main()
