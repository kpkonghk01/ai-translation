"""check 子命令的測試：Component id 缺少、多出、重覆，type 被改。從 main(argv) 進入，transport 注入假的。"""
import copy
import json
import os
import unittest

import gemini_samples
from fake_transport import FakeTransport
from fixtures import ARTICLE_ID, Workspace, component, good_translation, source_article, translation_with


class CheckTest(Workspace):
    def translate_and_check(self, translation):
        sample = gemini_samples.ok(json.dumps(translation, ensure_ascii=False))
        self.run_cli("translate", transport=FakeTransport(sample))
        code, out, err = self.run_cli("check")
        self.assertEqual(code, 0, err)
        return {row["component_id"]: row for row in self.read_rows()}

    def read_rows(self):
        with open(os.path.join(self.out_dir, "components.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def findings(self, rows, component_id):
        return [(f["dimension"], f["subtype"], f["severity"], f["source"]) for f in rows[component_id]["findings"]]

    def test_a_faithful_structure_has_a_row_per_component_and_no_findings(self):
        rows = self.translate_and_check(good_translation(source_article()))
        self.assertEqual(sorted(rows), ["9000001-001", "9000001-002", "9000001-003", "9000001-004", "tags", "title"])
        self.assertEqual({id_: r["source_type"] for id_, r in rows.items()},
                         {"title": "title", "tags": "tags", "9000001-001": "highlight", "9000001-002": "paragraph",
                          "9000001-003": "image", "9000001-004": "code"})
        self.assertEqual([r["findings"] for r in rows.values()], [[]] * 6)

    def test_a_missing_component_is_an_accuracy_omission_major(self):
        rows = self.translate_and_check(translation_with(lambda t: t["components"].remove(component(t, "002"))))
        self.assertEqual(self.findings(rows, "9000001-002"), [("Accuracy", "omission", "major", "deterministic")])
        self.assertEqual(sum(len(r["findings"]) for r in rows.values()), 1)

    def test_a_missing_title_or_tags_is_an_omission_too(self):
        for name, mutate, component_id in (
            ("title absent", lambda t: t.pop("title"), "title"),
            ("title empty", lambda t: t.update(title="  "), "title"),
            ("tags absent", lambda t: t.pop("tags"), "tags"),
            ("tags emptied", lambda t: t.update(tags=[]), "tags"),
        ):
            with self.subTest(name):
                rows = self.translate_and_check(translation_with(mutate))
                self.assertEqual(self.findings(rows, component_id), [("Accuracy", "omission", "major", "deterministic")])
                os.remove(os.path.join(self.out_dir, "runs.jsonl"))   # 下一個情況重新翻譯

    def test_an_extra_component_is_flagged_and_has_no_source_type(self):
        rows = self.translate_and_check(translation_with(lambda t: t["components"].append({"id": "9000001-099", "type": "paragraph", "text": "Added."})))
        self.assertIsNone(rows["9000001-099"]["source_type"])
        self.assertEqual(self.findings(rows, "9000001-099"), [("Locale & Format", "extra-component", "major", "deterministic")])

    def test_a_duplicated_id_is_flagged(self):
        rows = self.translate_and_check(translation_with(lambda t: t["components"].append(copy.deepcopy(component(t, "002")))))
        self.assertEqual(self.findings(rows, "9000001-002"), [("Locale & Format", "duplicate-component", "major", "deterministic")])

    def test_a_changed_type_is_flagged(self):
        rows = self.translate_and_check(translation_with(lambda t: component(t, "002").update(type="heading")))
        self.assertEqual(self.findings(rows, "9000001-002"), [("Locale & Format", "type-changed", "minor", "deterministic")])

    def test_a_component_using_a_reserved_id_is_an_extra_and_does_not_stand_in_for_the_title(self):
        sample = gemini_samples.ok(json.dumps(translation_with(
            lambda t: [t.pop("title"), t["components"].append({"id": "title", "type": "paragraph", "text": "x"})])))
        self.run_cli("translate", transport=FakeTransport(sample))
        self.assertEqual(self.run_cli("check")[0], 0)
        rows = [(r["source_type"], [f["subtype"] for f in r["findings"]]) for r in self.read_rows() if r["component_id"] == "title"]
        self.assertCountEqual(rows, [("title", ["omission"]), (None, ["extra-component"])])

    def test_only_ok_runs_are_checked(self):
        self.run_cli("translate", transport=FakeTransport(gemini_samples.prompt_blocked()))
        code, _, _ = self.run_cli("check")
        self.assertEqual(code, 0)
        self.assertEqual(self.read_rows(), [])

    def test_rerunning_check_gives_the_same_file(self):
        self.translate_and_check(good_translation(source_article()))
        with open(os.path.join(self.out_dir, "components.jsonl"), "rb") as f:
            first = f.read()
        self.run_cli("check")
        with open(os.path.join(self.out_dir, "components.jsonl"), "rb") as f:
            self.assertEqual(f.read(), first)

    def test_fails_without_writing_when_there_are_no_runs_or_a_translation_is_missing(self):
        code, _, err = self.run_cli("check")
        self.assertNotEqual(code, 0)
        self.assertIn("translate", err)
        self.assertFalse(os.path.exists(self.data_dir))

        self.run_cli("translate", transport=FakeTransport(gemini_samples.ok(json.dumps(good_translation(source_article())))))
        os.remove(os.path.join(self.out_dir, self.read_runs()[0]["translation"]))
        code, _, err = self.run_cli("check")
        self.assertNotEqual(code, 0)
        self.assertIn(ARTICLE_ID, err)
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "components.jsonl")))


if __name__ == "__main__":
    unittest.main()
