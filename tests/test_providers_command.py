"""providers <model>：列出 OpenRouter 上某個模型的 endpoint 和 provider，讓 Pan 確認允許清單。指令本身不寫入任何東西。"""
import contextlib
import io
import os
import unittest
from unittest import mock

import openrouter_samples as samples
from fake_transport import FakeTransport
from fixtures import FAKE_KEY
from translation_eval import cli
from translation_eval.transport import OFFLINE_ENV

KEY_ENV = "OPENROUTER_API_KEY"


class ProvidersCommandTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {KEY_ENV: FAKE_KEY, OFFLINE_ENV: "1"})
        env.start()
        self.addCleanup(env.stop)

    def run_command(self, *argv, transport=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["providers", *argv], transport=transport)
        return code, out.getvalue(), err.getvalue()

    def test_lists_each_endpoint_with_the_provider_name_and_the_slug_to_put_in_only(self):
        transport = FakeTransport(samples.endpoints(samples.endpoint("DeepInfra", "deepinfra"), samples.endpoint("Novita", "novita/fp8")))
        code, out, err = self.run_command("tencent/hy-mt2", transport=transport)
        self.assertEqual(code, 0, err)
        request = transport.requests[0]
        self.assertEqual((request.method, request.url), ("GET", "https://openrouter.ai/api/v1/models/tencent/hy-mt2/endpoints"))
        self.assertEqual(request.headers["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertIn("2 個 endpoint", out)
        for part in ("DeepInfra", "deepinfra", "Novita", "novita/fp8", "fp8"):
            self.assertIn(part, out)
        self.assertNotIn(FAKE_KEY, out + err)

    def test_prints_any_region_or_data_policy_field_the_api_provides_and_says_so_when_there_is_none(self):
        transport = FakeTransport(samples.endpoints(samples.endpoint("DeepInfra", "deepinfra", region="us-east", data_policy="no-training")))
        _, out, _ = self.run_command("tencent/hy-mt2", transport=transport)
        self.assertIn("region=us-east", out)
        self.assertIn("data_policy=no-training", out)
        transport = FakeTransport(samples.endpoints(samples.endpoint("DeepInfra", "deepinfra")))
        _, out, _ = self.run_command("tencent/hy-mt2", transport=transport)
        self.assertIn("API 沒有提供地區或資料政策的欄位", out)

    def test_it_writes_nothing(self):
        before = set(os.listdir("."))
        self.run_command("tencent/hy-mt2", transport=FakeTransport(samples.endpoints(samples.endpoint("DeepInfra", "deepinfra"))))
        self.assertEqual(set(os.listdir(".")), before)

    def test_a_dry_run_prints_the_address_and_sends_nothing(self):
        transport = FakeTransport()
        code, out, _ = self.run_command("tencent/hy-mt2", "--dry-run", transport=transport)
        self.assertEqual(code, 0)
        self.assertIn("https://openrouter.ai/api/v1/models/tencent/hy-mt2/endpoints", out)
        self.assertEqual(transport.requests, [])

    def test_a_missing_key_stops_before_any_request(self):
        del os.environ[KEY_ENV]
        transport = FakeTransport()
        code, _, err = self.run_command("tencent/hy-mt2", transport=transport)
        self.assertNotEqual(code, 0)
        self.assertIn(KEY_ENV, err)
        self.assertEqual(transport.requests, [])

    def test_a_model_that_is_not_author_slash_slug_is_rejected_before_any_request(self):
        transport = FakeTransport()
        for model in ("hy-mt2", "../etc/passwd", "a/b/c", "tencent/hy mt2", "tencent/hy-mt2?x=1", ""):
            with self.subTest(model):
                code, _, err = self.run_command(model, transport=transport)
                self.assertNotEqual(code, 0)
                self.assertIn("model", err)
        self.assertEqual(transport.requests, [])

    def test_failures_are_reported_with_the_status_and_not_as_an_empty_list(self):
        for name, sample in (("not found", samples.error(404)), ("unauthorised", samples.error(401)), ("html", samples.not_json(502)),
                             ("unknown format", samples.unknown_format()), ("no endpoints", samples.endpoints())):
            with self.subTest(name):
                code, out, err = self.run_command("tencent/hy-mt2", transport=FakeTransport(sample))
                self.assertNotEqual(code, 0)
                self.assertNotIn("0 個 endpoint", out)

    def test_the_real_transport_refuses_when_offline_so_a_forgotten_fake_cannot_reach_the_network(self):
        code, _, err = self.run_command("tencent/hy-mt2")
        self.assertNotEqual(code, 0)
        self.assertIn(OFFLINE_ENV, err)


if __name__ == "__main__":
    unittest.main()
