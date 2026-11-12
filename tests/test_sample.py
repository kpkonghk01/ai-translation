"""sample：從一個實驗抽出幾篇文章，每個模型只取第 1 次、只取完整文章，把紀錄與譯文原樣複製成新的實驗。從 main(argv) 進入。

每組（ground truth 的題材）抽固定數量的文章，對照文章另外抽；用固定種子，所以同樣的設定抽出來的一樣。只用虛構資料。"""
import contextlib
import io
import json
import os
import shutil
import unittest

import gemini_samples
from fake_transport import FakeTransport, FunctionTransport
from fixtures import Workspace, good_simplified_translation, good_translation, main_only_translation, simplified_response, source_article
from gate_helpers import ground_truth_for
from translation_eval import cli

IDS = ("9000001", "9000002", "9000003", "9000004", "9000005", "9000006", "9000007")
NEW_ID = "tracer-sample"


def user_text(request) -> str:
    return json.loads(request.body)["contents"][0]["parts"][0]["text"]


def reply(request):
    """依送來的文章回譯文：完整文章與只送標題與正文各回各的。文章 id 從 Component id 讀出來。"""
    payload = json.loads(user_text(request))
    article_id = payload["components"][0]["id"].split("-")[0]
    main_only = payload["tags"] == []
    translation = main_only_translation(article_id) if main_only else good_translation(source_article(article_id))
    return gemini_samples.ok(json.dumps(translation, ensure_ascii=False))


def three_groups_ground_truth():
    """一般題材 9000001–9000003、中國政治題材 9000004–9000005、對照 9000006；9000007 沒有 ground truth。"""
    gt = ground_truth_for(("9000001", "general"), ("9000002", "general"), ("9000003", "general"), ("9000004", "china-political"),
                          ("9000005", "china-political"))
    gt["articles"].append({"article_id": "9000006", "role": "control", "domain": "general", "note": "虛構的對照", "items": [], "source_defects": []})
    return gt


class SampleCase(Workspace):
    article_ids = IDS
    runs = 2
    input_variants = ["full", "main-only"]

    def setUp(self):
        super().setUp()
        self.use_ground_truth(three_groups_ground_truth())
        code, _, err = self.run_cli("translate", transport=FunctionTransport(reply))
        self.assertEqual(code, 0, err)

    @property
    def new_dir(self) -> str:
        return os.path.join(self.data_dir, "experiments", NEW_ID)

    @property
    def new_config(self) -> str:
        return os.path.join(os.path.dirname(self.config_path), f"{NEW_ID}.json")

    def sample(self, *extra):
        return self.run_cli("sample", "--id", NEW_ID, *extra)

    def sampled(self, *extra) -> list[dict]:
        code, out, err = self.sample(*extra)
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.new_dir, "runs.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def new_config_json(self) -> dict:
        with open(self.new_config, encoding="utf-8") as f:
            return json.load(f)


class WhatIsCopiedTest(SampleCase):
    def test_only_the_first_run_of_the_full_article_is_copied_for_the_chosen_articles(self):
        records = self.sampled("--per-group", "1", "--controls", "0")
        self.assertEqual({(r["run_index"], r["input_variant"]) for r in records}, {(1, "full")})
        self.assertEqual(len({r["article_id"] for r in records}), 2)   # 兩個組各 1 篇

    def test_the_records_are_copied_as_they_are_and_their_files_come_with_them(self):
        source = {(r["article_id"], r["run_index"], r["input_variant"]): r for r in self.read_runs()}
        for record in self.sampled("--per-group", "1", "--controls", "0"):
            self.assertEqual(record, source[(record["article_id"], 1, "full")])
            for name in ("translation", "raw_response"):
                with open(os.path.join(self.out_dir, record[name]), "rb") as original, open(os.path.join(self.new_dir, record[name]), "rb") as copy_:
                    self.assertEqual(original.read(), copy_.read())

    def test_controls_are_counted_separately_from_the_groups(self):
        records = self.sampled("--per-group", "1", "--controls", "1")
        self.assertEqual(len({r["article_id"] for r in records}), 3)
        self.assertIn("9000006", {r["article_id"] for r in records})

    def test_an_article_without_ground_truth_is_never_chosen(self):
        records = self.sampled("--per-group", "5", "--controls", "5")
        self.assertEqual({r["article_id"] for r in records}, set(IDS[:6]))   # 9000007 沒有 ground truth

    def test_asking_for_more_than_a_group_has_takes_the_whole_group(self):
        records = self.sampled("--per-group", "99", "--controls", "0")
        self.assertEqual({r["article_id"] for r in records}, set(IDS[:5]))

    def test_the_numbers_it_prints_are_counts_only(self):
        _, out, _ = self.sample("--per-group", "1", "--controls", "0")
        self.assertIn("一般題材 1 篇", out)
        self.assertIn("中國政治題材 1 篇", out)
        for article_id in IDS:
            self.assertNotIn(article_id, out)
        self.assertNotIn(source_article("9000001")["title"], out)


class ChoiceTest(SampleCase):
    def chosen(self, *extra) -> set:
        return {r["article_id"] for r in self.sampled(*extra)}

    def test_the_same_seed_chooses_the_same_articles_and_another_seed_may_not(self):
        first = self.chosen("--per-group", "1", "--controls", "0", "--seed", "1")
        os_remove_dir(self.new_dir, self.new_config)
        self.assertEqual(self.chosen("--per-group", "1", "--controls", "0", "--seed", "1"), first)
        seen = {frozenset(first)}
        for seed in range(2, 12):
            os_remove_dir(self.new_dir, self.new_config)
            seen.add(frozenset(self.chosen("--per-group", "1", "--controls", "0", "--seed", str(seed))))
        self.assertGreater(len(seen), 1)   # 種子有作用：十個種子不會全部抽到同一組

    def test_the_choice_does_not_depend_on_how_the_runs_turned_out(self):
        # 抽文章的依據只有種子與 ground truth：不能看結果挑
        first = self.chosen("--per-group", "1", "--controls", "0")
        os_remove_dir(self.new_dir, self.new_config)
        runs_path = os.path.join(self.out_dir, "runs.jsonl")
        with open(runs_path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
        for record in lines:
            record["outcome"] = "block" if record["article_id"] in first else "ok"
        with open(runs_path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines))
        self.assertEqual(self.chosen("--per-group", "1", "--controls", "0"), first)


def os_remove_dir(directory: str, config: str) -> None:
    shutil.rmtree(directory)
    os.remove(config)


class NewConfigTest(SampleCase):
    def test_the_config_has_the_chosen_articles_one_run_and_the_full_article_only(self):
        records = self.sampled("--per-group", "1", "--controls", "0")
        config = self.new_config_json()
        self.assertEqual(config["id"], NEW_ID)
        self.assertEqual(set(config["articles"]["ids"]), {r["article_id"] for r in records})
        self.assertEqual([(a["runs"], a["input_variants"]) for a in config["approaches"]], [(1, ["full"])])
        self.assertEqual(config["articles"]["dir"], self.articles_dir)

    def test_the_other_settings_are_carried_over(self):
        self.use_judging()
        self.sampled("--per-group", "1", "--controls", "0")
        config = self.new_config_json()
        self.assertEqual(config["ground_truth"], self.experiment()["ground_truth"])
        self.assertEqual(config["judging"], self.experiment()["judging"])
        self.assertEqual(config["directions"], ["trad-to-en"])

    def test_calibration_is_left_out_because_the_gold_set_points_at_articles_that_are_not_in_the_sample(self):
        self.extra_experiment["calibration"] = {"gold_set": os.path.join(self.tmp, "gold-set.json")}
        self.write_json(self.config_path, self.experiment())
        _, out, _ = self.sample("--per-group", "1", "--controls", "0")
        self.assertNotIn("calibration", self.new_config_json())
        self.assertIn("沒有帶校準設定", out)   # 說出來，免得以為漏了
        self.assertEqual(self.new_config_json()["ground_truth"], self.experiment()["ground_truth"])   # 其他設定照帶

    def test_without_calibration_in_the_source_there_is_nothing_to_say_about_it(self):
        _, out, _ = self.sample("--per-group", "1", "--controls", "0")
        self.assertNotIn("校準", out)

    def test_group_overrides_keep_only_the_chosen_articles_so_the_new_config_still_loads(self):
        self.use_ground_truth(three_groups_ground_truth(), group_overrides={"9000001": "q34", "9000002": "q34"})
        records = self.sampled("--per-group", "1", "--controls", "0")
        chosen = {r["article_id"] for r in records}
        overrides = self.new_config_json()["ground_truth"]["group_overrides"]
        self.assertEqual(set(overrides), chosen & {"9000001", "9000002"})
        self.assertEqual(len(overrides), 1)   # 兩篇覆寫的文章只抽中一篇
        self.run_new("translate", "--dry-run")   # 指向沒抽中的文章的覆寫會被設定驗證拒絕

    def test_the_new_experiment_is_complete_for_the_translation_step(self):
        self.sampled("--per-group", "1", "--controls", "0")
        out, err = self.run_new("translate", "--dry-run")
        self.assertIn("Run：0（已有紀錄而略過：2）", out)
        self.assertEqual(self.run_new("check")[1], "")

    def run_new(self, command: str, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([command, "--config", self.new_config, "--data-dir", self.data_dir, *extra], transport=FakeTransport())
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue(), err.getvalue()

    def test_a_note_says_where_the_sample_came_from_and_which_articles_are_in_it(self):
        self.sampled("--per-group", "1", "--controls", "0")
        with open(os.path.join(self.new_dir, "sample.json"), encoding="utf-8") as f:
            note = json.load(f)
        self.assertEqual((note["from"], note["seed"], note["per_group"], note["controls"]), ("tracer", 1, 1, 0))
        self.assertEqual(sorted(note["articles"]), sorted(self.new_config_json()["articles"]["ids"]))


class RefusalsTest(SampleCase):
    def assert_refused(self, needle: str, *extra) -> None:
        code, out, err = self.sample(*extra)
        self.assertNotEqual(code, 0)
        self.assertIn(needle, err)
        self.assertEqual(out, "")

    def test_it_will_not_overwrite_an_existing_experiment_or_config(self):
        self.sampled("--per-group", "1", "--controls", "0")
        before = self.new_config_json()
        self.assert_refused("已經存在", "--per-group", "2")
        self.assertEqual(self.new_config_json(), before)

    def test_a_clash_on_the_config_alone_writes_nothing(self):
        with open(self.new_config, "w", encoding="utf-8") as f:
            f.write("{}")
        self.assert_refused("已經存在", "--per-group", "1")
        self.assertFalse(os.path.exists(self.new_dir))

    def test_it_needs_ground_truth_because_the_groups_come_from_it(self):
        self.extra_experiment.pop("ground_truth")
        self.write_json(self.config_path, self.experiment())
        self.assert_refused("ground truth")
        self.assertFalse(os.path.exists(self.new_dir))

    def test_it_needs_runs_to_copy(self):
        os.remove(os.path.join(self.out_dir, "runs.jsonl"))
        self.assert_refused("沒有 Run 紀錄")

    def test_a_missing_translation_file_stops_it_before_anything_is_written(self):
        victim = next(r for r in self.read_runs() if r["input_variant"] == "full" and r["run_index"] == 1)
        os.remove(os.path.join(self.out_dir, victim["translation"]))
        self.assert_refused("缺少檔案", "--per-group", "99", "--controls", "5")
        self.assertFalse(os.path.exists(self.new_dir))
        self.assertFalse(os.path.exists(self.new_config))

    def test_the_new_id_must_be_a_plain_name(self):
        for bad in ("../x", "a/b", "", "x y"):
            with self.subTest(bad):
                code, _, err = self.run_cli("sample", "--id", bad)
                self.assertNotEqual(code, 0)
                self.assertIn("--id 要是由字母、數字", err)
        self.assertEqual(os.listdir(self.data_dir), ["experiments"])

    def test_counts_must_make_sense(self):
        for bad in (("--per-group", "0"), ("--per-group", "-1"), ("--controls", "-1")):
            with self.subTest(bad):
                self.assertNotEqual(self.sample(*bad)[0], 0)


class BaselineTest(Workspace):
    article_ids = ("9000001", "9000002")
    directions = ["trad-to-simplified"]

    def test_the_baseline_run_of_a_chosen_article_comes_along(self):
        self.use_ground_truth(ground_truth_for(("9000001", "general"), ("9000002", "general")))
        baseline_dir = os.path.join(self.tmp, "baseline")
        for article_id in self.article_ids:
            self.write_baseline(baseline_dir, article_id, good_simplified_translation(article_id))
        self.use_baseline(baseline_dir)
        self.assertEqual(self.run_cli("import-baseline")[0], 0)
        self.assertEqual(self.run_cli("translate", transport=FakeTransport(simplified_response("9000001"), simplified_response("9000002")))[0], 0)
        code, _, err = self.run_cli("sample", "--id", NEW_ID, "--per-group", "1", "--controls", "0")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.data_dir, "experiments", NEW_ID, "runs.jsonl"), encoding="utf-8") as f:
            records = [json.loads(line) for line in f]
        self.assertEqual(sorted(r["approach"] for r in records), ["a", "e"])
        self.assertEqual(len({r["article_id"] for r in records}), 1)
        with open(os.path.join(os.path.dirname(self.config_path), f"{NEW_ID}.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["baselines"], self.experiment()["baselines"])


if __name__ == "__main__":
    unittest.main()
