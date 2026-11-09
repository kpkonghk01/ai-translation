"""import-baseline：把方案 E（Current service）已重現的 baseline 匯入成 Run。從 main(argv) 進入，不連網、不需要 key。"""
import json
import os
import unittest

from fake_transport import FakeTransport
from fixtures import TERM_LIST, Workspace, good_simplified_translation, simplified_response

LABEL = "fictional-service-1"


class ImportBaselineCase(Workspace):
    directions = ["trad-to-simplified"]
    article_ids = ("9000001", "9000002")

    def setUp(self):
        super().setUp()
        self.baseline_dir = os.path.join(self.tmp, "baseline")
        for article_id in self.article_ids:
            self.write_baseline(self.baseline_dir, article_id, good_simplified_translation(article_id), LABEL)
        self.use_baseline(self.baseline_dir)

    def import_baseline(self):
        return self.run_cli("import-baseline")

    def assert_rejected(self, needle):
        code, out, err = self.import_baseline()
        self.assertNotEqual(code, 0)
        self.assertIn(needle, err)
        self.assertEqual(out, "")
        self.assertFalse(os.path.exists(self.data_dir), "被拒絕時不可以寫出任何東西")


class ImportTest(ImportBaselineCase):
    def test_every_article_becomes_an_ok_run_of_the_baseline_approach_with_no_tokens_or_latency(self):
        code, out, err = self.import_baseline()
        self.assertEqual(code, 0, err)
        records = self.read_runs()
        self.assertEqual(sorted(r["article_id"] for r in records), ["9000001", "9000002"])
        for record in records:
            self.assertEqual((record["approach"], record["direction"], record["input_variant"], record["run_index"], record["outcome"]),
                             ("e", "trad-to-simplified", "full", 1, "ok"))
            self.assertEqual(record["baseline_label"], LABEL)
            self.assertEqual([record[k] for k in ("input_tokens", "output_tokens", "thought_tokens", "latency_seconds", "raw_response")], [None] * 5)
            self.assertEqual(record["attempts"], [])
            self.assertIsNone(record["prompt_id"])
        self.assertIn("2", out)

    def test_the_translation_is_the_baseline_without_its_meta(self):
        self.import_baseline()
        record = next(r for r in self.read_runs() if r["article_id"] == "9000001")
        with open(os.path.join(self.out_dir, record["translation"]), encoding="utf-8") as f:
            self.assertEqual(json.load(f), good_simplified_translation("9000001"))

    def test_importing_again_adds_nothing(self):
        self.import_baseline()
        code, _, err = self.import_baseline()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.read_runs()), 2)

    def test_baseline_files_for_articles_outside_the_experiment_are_ignored(self):
        self.write_baseline(self.baseline_dir, "8000001", good_simplified_translation("9000001"), LABEL)
        self.import_baseline()
        self.assertEqual(len(self.read_runs()), 2)

    def test_the_terminal_prints_only_counts(self):
        _, out, _ = self.import_baseline()
        self.assertNotIn("虚", out)


class RejectionTest(ImportBaselineCase):
    def test_a_baseline_without_a_label_is_rejected(self):
        for name, label in (("missing", None), ("empty", ""), ("blank", "  "), ("not a string", 5)):
            with self.subTest(name):
                self.write_baseline(self.baseline_dir, "9000002", good_simplified_translation("9000002"), label)
                self.assert_rejected("9000002")

    def test_one_baseline_with_two_different_labels_is_rejected(self):
        self.write_baseline(self.baseline_dir, "9000002", good_simplified_translation("9000002"), "another-label")
        self.assert_rejected("標籤")

    def test_a_missing_baseline_file_is_rejected_instead_of_skipped(self):
        os.remove(os.path.join(self.baseline_dir, "9000002.json"))
        self.assert_rejected("9000002")

    def test_a_baseline_file_with_the_wrong_shape_is_rejected(self):
        broken = good_simplified_translation("9000002")
        broken["components"][1]["text"] = 123
        self.write_baseline(self.baseline_dir, "9000002", broken, LABEL)
        self.assert_rejected("9000002")

    def test_a_label_that_differs_from_the_one_already_imported_is_rejected(self):
        self.import_baseline()
        for article_id in self.article_ids:
            self.write_baseline(self.baseline_dir, article_id, good_simplified_translation(article_id), "newer-label")
        code, _, err = self.import_baseline()
        self.assertNotEqual(code, 0)
        self.assertIn("標籤", err)
        self.assertEqual({r["baseline_label"] for r in self.read_runs()}, {LABEL})   # 舊紀錄原樣

    def test_nothing_is_written_when_only_one_of_several_files_is_bad(self):
        self.write_baseline(self.baseline_dir, "9000002", good_simplified_translation("9000002"), None)
        self.assert_rejected("9000002")

    def test_an_experiment_without_baselines_has_nothing_to_import(self):
        del self.extra_experiment["baselines"]
        self.write_json(self.config_path, self.experiment())
        self.assert_rejected("baselines")

    def test_invalid_baseline_settings_are_rejected(self):
        for name, entry, needle in (
            ("same id as an Approach", {"id": "a", "dir": self.baseline_dir, "direction": "trad-to-simplified"}, "重覆"),
            ("unsafe id", {"id": "../e", "dir": self.baseline_dir, "direction": "trad-to-simplified"}, "id"),
            ("unknown direction", {"id": "e", "dir": self.baseline_dir, "direction": "trad-to-klingon"}, "direction"),
        ):
            with self.subTest(name):
                self.extra_experiment["baselines"] = [entry]
                self.write_json(self.config_path, self.experiment())
                self.assert_rejected(needle)


class CheckAndReportTest(ImportBaselineCase):
    def run_all(self):
        self.import_baseline()
        self.assertEqual(self.run_cli("check")[0], 0)
        code, _, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            return f.read()

    def test_the_baseline_goes_through_the_same_check(self):
        residual = good_simplified_translation("9000001")
        residual["components"][1]["text"] = "市民陳虚构表示，工程拖延令他十分不满。"   # 「陳」是繁體
        self.write_baseline(self.baseline_dir, "9000001", residual, LABEL)
        self.import_baseline()
        self.run_cli("check")
        with open(os.path.join(self.out_dir, "components.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        spans = [(r["article_id"], f["target_span"]) for r in rows for f in r["findings"] if f["subtype"] == "residual-traditional"]
        self.assertEqual(spans, [("9000001", "陳")])

    def test_the_report_lists_the_baseline_label_and_has_a_section_for_it(self):
        text = self.run_all()
        self.assertIn(f"- e：{LABEL}（2 個 Run）", text.split("## Baseline")[1].split("\n## ")[0])
        self.assertIn("## e × trad-to-simplified\n", text)
        section = text.split("## e × trad-to-simplified\n")[1].split("\n## ")[0]
        self.assertIn("- Run：計畫 2，已有紀錄 2", section)
        self.assertIn("- Block 率：按 Run 0/2（0.0%）；按文章 0/2（0.0%）", section)
        self.assertIn("已檢查 2 個 ok 的 Run", section)

    def test_the_report_header_does_not_count_baseline_runs_as_translated_with_a_prompt(self):
        text = self.run_all()
        self.assertNotIn("None", text)
        self.assertIn("## Prompt\n\n", text)

    def test_a_term_list_does_not_make_check_refuse_the_baseline_runs(self):
        self.use_term_list(TERM_LIST)
        self.import_baseline()
        code, _, err = self.run_cli("check")
        self.assertEqual(code, 0, err)

    def test_an_llm_approach_and_the_baseline_share_one_report(self):
        self.import_baseline()
        self.run_cli("translate", transport=FakeTransport(simplified_response("9000001"), simplified_response("9000002")))
        self.assertEqual(self.run_cli("check")[0], 0)
        self.assertEqual(self.run_cli("report")[0], 0)
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## a × trad-to-simplified\n", text)
        self.assertIn("## e × trad-to-simplified\n", text)


if __name__ == "__main__":
    unittest.main()
