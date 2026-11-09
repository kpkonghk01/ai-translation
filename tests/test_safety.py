"""驗證與安全：設定不合法、缺 key、key 外洩、離線防線。全部不連網。"""
import json
import os
import unittest
from unittest import mock

import gemini_samples
from fake_transport import FakeTransport
from fixtures import APPROACH, FAKE_KEY, KEY_ENV, Workspace, good_translation, ok_response, source_article
from translation_eval.transport import OFFLINE_ENV, Request, TransportFailure, TransportForbidden, UrllibTransport


class InvalidInputTest(Workspace):
    """輸入或設定驗證失敗：非 0 結束，列出原因，不送請求，不寫出任何檔案（連 data 目錄也不建）。"""

    def test_invalid_experiment_config(self):
        cases = {
            "a misspelt field is not silently ignored": (lambda e: e.update(approches=e.pop("approaches")), "approches"),
            "zero runs": (lambda e: e["approaches"][0].update(runs=0), "runs"),
            "no articles": (lambda e: e["articles"].update(ids=[]), "ids"),
            "duplicate article id": (lambda e: e["articles"].update(ids=["9000001", "9000001"]), "重覆"),
            "article id that is not a safe file name": (lambda e: e["articles"].update(ids=["../escape"]), "ids"),
            "direction that is not supported yet": (lambda e: e.update(directions=["trad-to-simplified"]), "directions"),
            "experiment id that is not a safe directory name": (lambda e: e.update(id="../escape"), "id"),
        }
        for name, (mutate, needle) in cases.items():
            with self.subTest(name):
                self.rewrite_experiment(mutate)
                self.assert_fails_cleanly(needle)

    def test_invalid_approach_config(self):
        cases = {
            "key variable of another provider": ({"api_key_env": "ANTHROPIC_API_KEY"}, "GEMINI_API_KEY"),
            "concurrency above one": ({"concurrency": 4}, "concurrency"),
            "model that is not a plain model name": ({"model": "../other:path"}, "model"),
            "unknown provider": ({"provider": "somewhere-else"}, "provider"),
            "request mode not implemented yet": ({"request_mode": "per-component"}, "request_mode"),
            "unknown field": ({"input_limit": 1000}, "input_limit"),
        }
        for name, (change, needle) in cases.items():
            with self.subTest(name):
                self.write_json(self.approach_path, {**APPROACH, **change})
                self.assert_fails_cleanly(needle)

    def test_unreadable_files(self):
        with open(self.approach_path, "w") as f:
            f.write("{not json")
        self.assert_fails_cleanly("不是合法的 JSON")

    def test_same_approach_listed_twice(self):
        self.rewrite_experiment(lambda e: e["approaches"].append(dict(e["approaches"][0])))
        self.assert_fails_cleanly("Approach id 重覆")

    def test_article_the_converter_flagged_as_incomplete_is_rejected(self):
        flagged = source_article()
        flagged["meta"]["unknown"] = ["block:poll"]
        self.write_json(os.path.join(self.articles_dir, "9000001.json"), flagged)
        self.assert_fails_cleanly("meta.unknown")

    def test_missing_or_broken_article_file(self):
        os.remove(os.path.join(self.articles_dir, "9000001.json"))
        self.assert_fails_cleanly("9000001")
        broken = source_article()
        del broken["components"][0]["id"]
        self.write_json(os.path.join(self.articles_dir, "9000001.json"), broken)
        self.assert_fails_cleanly("components")

    def test_corrupt_existing_run_record_stops_the_run_instead_of_being_skipped(self):
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        with open(os.path.join(self.out_dir, "runs.jsonl"), "a") as f:
            f.write("this is not a run record\n")
        transport = FakeTransport()
        code, _, err = self.run_cli("translate", transport=transport)
        self.assertNotEqual(code, 0)
        self.assertIn("runs.jsonl", err)
        self.assertEqual(transport.requests, [])


class KeyTest(Workspace):
    def test_missing_key_stops_before_any_request_and_prints_only_the_variable_name(self):
        for value in (None, ""):
            with self.subTest(repr(value)):
                if value is None:
                    os.environ.pop(KEY_ENV, None)
                else:
                    os.environ[KEY_ENV] = value
                transport = FakeTransport()

                code, out, err = self.run_cli("translate", transport=transport)

                self.assertNotEqual(code, 0)
                self.assertIn(KEY_ENV, err)
                self.assertEqual(transport.requests, [])
                self.assertFalse(os.path.exists(self.data_dir))

    def test_the_key_is_sent_but_never_appears_in_any_output_file_or_message(self):
        # 最壞的情況：transport 的錯誤訊息和伺服器的回應都把 key 帶了回來
        # 連譯文和模型版本欄位都帶著 key
        translation = good_translation(source_article())
        translation["title"] = f"Title {FAKE_KEY}"
        leaking = gemini_samples.with_fields(gemini_samples.ok(json.dumps(translation)), echo=f"you sent {FAKE_KEY}",
                                             modelVersion=f"model-{FAKE_KEY}")
        transport = FakeTransport(TransportFailure(f"connection failed for {FAKE_KEY}"), leaking)

        code, out, err = self.run_cli("translate", transport=transport)

        self.assertEqual(code, 0)
        self.assertEqual({r.headers["x-goog-api-key"] for r in transport.requests}, {FAKE_KEY})   # key 確實用來送出請求
        self.assertNotIn(FAKE_KEY, self.all_output_text())
        self.assertNotIn(FAKE_KEY, out + err)
        self.assertIn("runs.jsonl", os.listdir(self.out_dir))   # 上面的檢查不是因為沒有輸出而通過


class OfflineTest(Workspace):
    def setUp(self):
        super().setUp()
        for target in ("socket.socket.connect", "socket.getaddrinfo"):
            patcher = mock.patch(target, side_effect=AssertionError("離線模式下不可以連網"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_forgetting_to_inject_a_transport_fails_instead_of_reaching_the_network(self):
        code, _, err = self.run_cli("translate")   # 沒有注入假 transport

        self.assertNotEqual(code, 0)
        self.assertIn(OFFLINE_ENV, err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "runs.jsonl")), "被拒絕的請求不可以記成 Run 的結果")

    def test_the_real_transport_refuses_when_offline(self):
        with self.assertRaises(TransportForbidden):
            UrllibTransport().send(Request("GET", "https://example.invalid/", {}, b"", 1))

    def test_the_real_transport_only_sends_https(self):
        del os.environ[OFFLINE_ENV]
        with self.assertRaises(TransportForbidden):
            UrllibTransport().send(Request("GET", "http://example.invalid/", {}, b"", 1))
        with self.assertRaises(TransportForbidden):
            UrllibTransport().send(Request("GET", "file:///etc/hosts", {}, b"", 1))


if __name__ == "__main__":
    unittest.main()
