"""阿里雲 DashScope（方案 B）：只送新加坡 endpoint、內容審查記為 Block、header 只在設定開啟時才送。從 main(argv) 進入。"""
import json
import os
import shutil
import unittest
from unittest import mock

import dashscope_samples as samples
from fake_transport import FakeTransport
from fixtures import FAKE_KEY, Workspace, good_translation, source_article

KEY_ENV = "DASHSCOPE_API_KEY"
DEFAULT_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
APPROACH = {"id": "b", "family": "qwen", "provider": "dashscope", "model": "qwen-plus", "api_key_env": KEY_ENV,
            "timeout_seconds": 30, "retry": {"max_attempts": 3, "base_delay_seconds": 0}}


def ok_response():
    return samples.ok(json.dumps(good_translation(source_article()), ensure_ascii=False))


class DashscopeCase(Workspace):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {KEY_ENV: FAKE_KEY})
        env.start()
        self.addCleanup(env.stop)
        self.use_approach(APPROACH)

    def use_approach(self, approach):
        self.write_json(self.approach_path, approach)

    def translate(self, *scripted):
        transport = FakeTransport(*scripted)
        code, out, err = self.run_cli("translate", transport=transport)
        return code, transport, out, err


class RequestTest(DashscopeCase):
    def sent(self, approach=None):
        shutil.rmtree(self.data_dir, ignore_errors=True)   # 同一個測試會呼叫好幾次；有紀錄的 Run 會被略過
        if approach:
            self.use_approach(approach)
        _, transport, _, err = self.translate(ok_response())
        self.assertEqual(len(transport.requests), 1, err)
        return transport.requests[0]

    def test_posts_to_the_singapore_endpoint_with_the_key_as_a_bearer_token_and_not_in_the_url(self):
        request = self.sent()
        self.assertEqual((request.method, request.url), ("POST", DEFAULT_URL))
        self.assertEqual(request.headers["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, request.url)

    def test_the_body_has_the_model_a_system_and_a_user_message_and_asks_for_json(self):
        body = json.loads(self.sent().body)
        self.assertEqual(body["model"], "qwen-plus")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(json.loads(body["messages"][1]["content"])["title"], source_article()["title"])

    def test_temperature_and_output_limit_are_sent_only_when_configured(self):
        self.assertNotIn("temperature", json.loads(self.sent().body))
        body = json.loads(self.sent({**APPROACH, "temperature": 0, "max_output_tokens": 4000}).body)
        self.assertEqual((body["temperature"], body["max_tokens"]), (0, 4000))

    def test_the_workspace_specific_singapore_domain_is_accepted(self):
        url = "https://ws123abc.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
        self.assertEqual(self.sent({**APPROACH, "base_url": url}).url, url + "/chat/completions")

    def test_the_data_inspection_header_is_not_sent_unless_configured(self):
        self.assertNotIn("X-DashScope-DataInspection", self.sent().headers)

    def test_the_data_inspection_header_is_sent_with_exactly_the_configured_value(self):
        value = '{"input":"cip","output":"cip"}'
        self.assertEqual(self.sent({**APPROACH, "data_inspection": value}).headers["X-DashScope-DataInspection"], value)


class ConfigTest(DashscopeCase):
    def test_every_other_endpoint_is_rejected_before_any_request(self):
        for name, url in (
            ("beijing", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            ("us", "https://dashscope-us.aliyuncs.com/compatible-mode/v1"),
            ("beijing workspace domain", "https://ws123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
            ("hong kong workspace domain", "https://ws123.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1"),
            ("plain http", "http://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
            ("another host", "https://example.invalid/compatible-mode/v1"),
            ("look-alike host", "https://dashscope-intl.aliyuncs.com.example.invalid/compatible-mode/v1"),
            ("credentials in the url", "https://user:pw@dashscope-intl.aliyuncs.com/compatible-mode/v1"),
            ("a port", "https://dashscope-intl.aliyuncs.com:8443/compatible-mode/v1"),
            ("another path", "https://dashscope-intl.aliyuncs.com/api/v1"),
            ("a query", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1?x=1"),
            # 下面這些 urlsplit 解析後看起來沒問題，但原樣送出就是另一個網址，所以要對原始字串驗證
            ("a trailing question mark", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1?"),
            ("a trailing hash", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1#"),
            ("a line break", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1\n"),
            ("a tab inside", "https://dashscope-intl.aliyuncs.com/compatible-mode/\tv1"),
            ("port zero", "https://dashscope-intl.aliyuncs.com:0/compatible-mode/v1"),
            ("empty credentials", "https://@dashscope-intl.aliyuncs.com/compatible-mode/v1"),
            ("an uppercase scheme", "HTTPS://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
            ("an uppercase host", "https://DASHSCOPE-INTL.aliyuncs.com/compatible-mode/v1"),
            ("a leading space", " https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
            ("a trailing dot in the host", "https://dashscope-intl.aliyuncs.com./compatible-mode/v1"),
        ):
            with self.subTest(name):
                self.use_approach({**APPROACH, "base_url": url})
                self.assert_fails_cleanly("base_url")

    def test_settings_that_belong_to_another_provider_are_rejected(self):
        gemini = {"id": "a", "family": "gemini", "provider": "gemini", "model": "gemini-3.1-flash-lite", "api_key_env": "GEMINI_API_KEY"}
        for name, approach, needle in (
            ("base_url on gemini", {**gemini, "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"}, "base_url"),
            ("data_inspection on gemini", {**gemini, "data_inspection": "{}"}, "data_inspection"),
            ("another provider's key variable", {**APPROACH, "api_key_env": "GEMINI_API_KEY"}, "DASHSCOPE_API_KEY"),
            ("a header value with a line break", {**APPROACH, "data_inspection": '{"input":"cip"}\r\nX-Evil: 1'}, "data_inspection"),
            ("an empty header value", {**APPROACH, "data_inspection": ""}, "data_inspection"),
        ):
            with self.subTest(name):
                self.use_approach(approach)
                self.assert_fails_cleanly(needle)


class ResultClassTest(DashscopeCase):
    def test_each_response_is_recorded_as_its_result_class(self):
        partial = '{"title": "Fictional'
        cases = {
            "ok": (ok_response(), "ok", None),
            "content moderation, error object": (samples.data_inspection_failed("object"), "block", "DataInspectionFailed"),
            "content moderation, top-level code": (samples.data_inspection_failed("top"), "block", "DataInspectionFailed"),
            "content moderation with throttling status": (samples.error(429, "DataInspectionFailed"), "block", "DataInspectionFailed"),
            "content moderation with server error status": (samples.error(503, "data_inspection_failed"), "block", "DataInspectionFailed"),
            "content moderation sent with status 200": (samples.error(200, "DataInspectionFailed"), "block", "DataInspectionFailed"),
            "faq rule": (samples.error(400, "FaqRuleBlocked"), "block", "FaqRuleBlocked"),
            "custom rule": (samples.error(400, "CustomRoleBlocked"), "block", "CustomRoleBlocked"),
            "ip infringement": (samples.error(400, "IPInfringementSuspect"), "block", "IPInfringementSuspect"),
            "content filter finish": (samples.finished(partial, "content_filter"), "block", "output:content_filter"),
            "length": (samples.finished(partial, "length"), "truncated", None),
            "refusal field": (samples.provider_refusal(), "refusal", None),
            "refusal wording": (samples.ok("I'm sorry, but I can't help with translating this article."), "refusal", None),
            "prose that is not a refusal": (samples.ok("Here is the translation of the article."), "malformed", None),
            "json that is not an article": (samples.ok('{"answer": 42}'), "malformed", None),
            "invalid api key": (samples.invalid_key(), "error", "HTTP 401 invalid_api_key"),
            "media download problem": (samples.error(400, "InvalidParameter.DataInspection"), "error", "HTTP 400 InvalidParameter.DataInspection"),
            "unknown format": (samples.unknown_format(), "error", None),
            "html with 200": (samples.not_json(), "error", None),
            "tool calls finish": (samples.finished("", "tool_calls"), "error", None),
            "no finish reason": (samples.finished("{}", None), "error", None),
            "refusal that is not a string": (samples.malformed_message({"content": "{}", "refusal": {"x": 1}}), "error", None),
            "html with a 400 status": (samples.not_json(400), "error", "HTTP 400"),
            "deeply nested json": (samples.deeply_nested(), "error", None),
            "deeply nested json with an error status": (samples.deeply_nested(400), "error", "HTTP 400"),
            "no content": (samples.malformed_message({"content": None}), "error", None),
            "content is a list": (samples.malformed_message({"content": ["x"]}), "error", None),
            "message is not an object": (samples.malformed_message("x"), "error", None),
            "token count is not a number": (samples.with_fields(ok_response(), usage={"prompt_tokens": "many"}), "error", None),
            "model is not a string": (samples.with_fields(ok_response(), model=["x"]), "error", None),
        }
        for name, (sample, outcome, reason) in cases.items():
            with self.subTest(name):
                shutil.rmtree(self.data_dir, ignore_errors=True)
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
        partial = '{"title": "Fictional'
        for name, mutate in (
            ("message is null", {"message": None}),
            ("usage is a list", {"usage": []}),
            ("a token count is a float", {"usage": {"prompt_tokens": 1.5}}),
            ("model is a number", {"model": 5}),
        ):
            with self.subTest(name):
                shutil.rmtree(self.data_dir, ignore_errors=True)
                sample = samples.finished(partial, "content_filter")
                body = json.loads(sample.body)
                if "message" in mutate:
                    body["choices"][0]["message"] = mutate["message"]
                else:
                    body.update(mutate)
                self.translate(samples._response(200, body))
                self.assertEqual(self.read_runs()[0]["outcome"], "block")

    def test_tokens_and_the_model_version_come_from_the_response(self):
        self.translate(ok_response())
        [record] = self.read_runs()
        self.assertEqual((record["input_tokens"], record["output_tokens"], record["thought_tokens"]), (120, 80, 30))
        self.assertEqual((record["model_version"], record["provider"], record["approach"]), (samples.MODEL_VERSION, "dashscope", "b"))

    def test_retries_throttling_and_server_errors_but_not_a_content_block(self):
        code, transport, _, err = self.translate(samples.rate_limited(), samples.server_error(503), ok_response())
        self.assertEqual((code, len(transport.requests)), (0, 3), err)
        self.assertEqual([a["http_status"] for a in self.read_runs()[0]["attempts"]], [429, 503, 200])

    def test_a_block_keeps_its_reason_in_the_report_apart_from_other_failures(self):
        self.rewrite_experiment(lambda e: e["approaches"][0].update(runs=3))
        self.translate(samples.data_inspection_failed(), samples.data_inspection_failed("top"), samples.invalid_key())
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("- Block 率：按 Run 2/3（66.7%）；按文章 1/1（100.0%）；原因分佈：DataInspectionFailed 2", text)
        self.assertIn("error 1/3（33.3%）", text)


class SafetyTest(DashscopeCase):
    def test_a_missing_key_stops_before_any_request_and_names_only_the_variable(self):
        del os.environ[KEY_ENV]
        code, transport, _, err = self.translate()
        self.assertNotEqual(code, 0)
        self.assertIn(KEY_ENV, err)
        self.assertEqual(transport.requests, [])

    def test_the_key_never_reaches_an_output_file_even_when_the_server_echoes_it(self):
        leaking = samples.with_fields(ok_response(), echo=f"you sent {FAKE_KEY}", model=f"qwen-{FAKE_KEY}")
        self.translate(leaking)
        self.assertNotIn(FAKE_KEY, self.all_output_text())

    def test_the_dry_run_shows_the_endpoint_and_sends_nothing(self):
        del os.environ[KEY_ENV]
        transport = FakeTransport()
        code, out, _ = self.run_cli("translate", "--dry-run", transport=transport)
        self.assertEqual(code, 0)
        self.assertIn(DEFAULT_URL, out)
        self.assertEqual(transport.requests, [])


if __name__ == "__main__":
    unittest.main()
