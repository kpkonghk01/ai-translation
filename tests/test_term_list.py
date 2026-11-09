"""Term List：格式驗證、注入翻譯 prompt、hash 記入 Run 紀錄。從 main(argv) 進入，transport 注入假的。"""
import copy
import json
import os
import re
import shutil
import unittest

from fake_transport import FakeTransport
from fixtures import TERM_LIST, Workspace, ok_response


def system_prompt(request) -> str:
    return json.loads(request.body)["systemInstruction"]["parts"][0]["text"]


class InjectionTest(Workspace):
    def sent_prompt(self) -> str:
        transport = FakeTransport(ok_response())
        self.assertEqual(self.run_cli("translate", transport=transport)[0], 0)
        return system_prompt(transport.requests[0])

    def test_the_prompt_lists_only_the_terms_that_appear_in_the_article(self):
        self.use_term_list(TERM_LIST)
        prompt = self.sent_prompt()
        self.assertIn("虛構市政廳", prompt)        # 出現在標題
        self.assertIn("Fictional City Hall", prompt)
        self.assertIn("陳虛構", prompt)            # 出現在段落
        self.assertIn("Chan Fictional", prompt)
        self.assertIn("Fictional Chan", prompt)    # 多於一個允許的譯法都列出
        self.assertNotIn("從未出現的詞", prompt)
        self.assertNotIn("Never Mentioned", prompt)

    def test_there_is_no_terminology_section_without_a_term_list_or_without_a_matching_term(self):
        without = self.sent_prompt()
        self.assertNotIn("Terminology", without)

        shutil.rmtree(self.data_dir)
        self.use_term_list({"terms": [TERM_LIST["terms"][2]]})   # 只有不會出現的那一條
        self.assertEqual(self.sent_prompt(), without)

    def test_a_term_is_found_even_when_the_source_has_stray_whitespace_inside_it(self):
        spaced = {"terms": [{"source": "虛 構 市政廳", "category": "organization", "translations": {"trad-to-en": ["Spaced Hall"]}}]}
        self.use_term_list(spaced)
        self.assertIn("Spaced Hall", self.sent_prompt())

    def test_the_run_record_stores_a_hash_of_the_term_list_that_ignores_formatting(self):
        path = self.use_term_list(TERM_LIST)
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        [record] = self.read_runs()
        self.assertRegex(record["term_list_hash"], r"^[0-9a-f]{64}$")

        shutil.rmtree(self.data_dir)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(TERM_LIST, f, ensure_ascii=False, indent=4)   # 只是換了排版
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        self.assertEqual(self.read_runs()[0]["term_list_hash"], record["term_list_hash"])

        shutil.rmtree(self.data_dir)
        changed = copy.deepcopy(TERM_LIST)
        changed["terms"][0]["translations"]["trad-to-en"] = ["Another Rendering"]
        self.write_json(path, changed)
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        self.assertNotEqual(self.read_runs()[0]["term_list_hash"], record["term_list_hash"])

    def test_without_a_term_list_the_hash_is_empty(self):
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        self.assertIsNone(self.read_runs()[0]["term_list_hash"])

    def test_the_dry_run_estimate_counts_the_injected_terms(self):
        self.use_term_list(TERM_LIST)
        real = FakeTransport(ok_response())
        self.run_cli("translate", transport=real)
        body = json.loads(real.requests[0].body)
        sent_chars = len(body["systemInstruction"]["parts"][0]["text"]) + len(body["contents"][0]["parts"][0]["text"])
        shutil.rmtree(self.data_dir)

        _, out, _ = self.run_cli("translate", "--dry-run", transport=FakeTransport())

        self.assertIn(f"估計輸入字數：{sent_chars}", out)


class ValidationTest(Workspace):
    """格式不合法的 Term List：非 0 結束，不送請求，不寫出任何檔案，錯誤訊息不抄條目的文字。"""

    def term_list_with(self, mutate) -> dict:
        term_list = copy.deepcopy(TERM_LIST)
        mutate(term_list)
        self.use_term_list(term_list)
        return term_list

    def test_invalid_formats_are_rejected(self):
        cases = {
            "unknown category": (lambda t: t["terms"][0].update(category="slang"), "category"),
            "unknown severity": (lambda t: t["terms"][0].update(severity="fatal"), "severity"),
            "misspelt field": (lambda t: t["terms"][0].update(translation=t["terms"][0].pop("translations")), "translation"),
            "no direction at all": (lambda t: t["terms"][0].update(translations={}), "translations"),
            "direction that is not supported yet": (lambda t: t["terms"][0].update(translations={"trad-to-simplified": ["x"]}), "translations"),
            "empty list of renderings": (lambda t: t["terms"][0]["translations"].update({"trad-to-en": []}), "trad-to-en"),
            "blank rendering": (lambda t: t["terms"][0]["translations"].update({"trad-to-en": ["  "]}), "trad-to-en"),
            "blank source": (lambda t: t["terms"][0].update(source="  "), "source"),
            "blank exception": (lambda t: t["terms"][1].update(simplified_exceptions=[""]), "simplified_exceptions"),
            "no terms key": (lambda t: (t.pop("terms"), t.update(entries=[])), "terms"),
        }
        for name, (mutate, needle) in cases.items():
            with self.subTest(name):
                self.term_list_with(mutate)
                self.assert_fails_cleanly(needle)

    def test_duplicate_entries_are_rejected_without_echoing_the_term(self):
        # 連空白不同的寫法也算重覆
        self.term_list_with(lambda t: t["terms"].append({**t["terms"][0], "source": "虛構 市政廳"}))
        code, _, err = self.run_cli("translate", transport=FakeTransport())
        self.assertNotEqual(code, 0)
        self.assertIn("重覆", err)
        self.assertIn("terms[3]", err)
        self.assertNotIn("市政廳", err)
        self.assertFalse(os.path.exists(self.data_dir))

    def test_a_missing_or_unreadable_term_list_is_rejected(self):
        path = self.use_term_list(TERM_LIST)
        with open(path, "w") as f:
            f.write("{not json")
        self.assert_fails_cleanly("不是合法的 JSON")
        os.remove(path)
        self.assert_fails_cleanly("讀不到檔案")

    def test_a_valid_list_with_every_optional_field_is_accepted(self):
        self.use_term_list(TERM_LIST)
        code, _, err = self.run_cli("translate", "--dry-run", transport=FakeTransport())
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
