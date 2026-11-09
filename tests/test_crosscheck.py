"""Ground truth 交叉檢驗：模型只看原文、盲選句子與事實、與 ground truth 對照出分歧清單，從不改動 ground truth 檔。
從 main(argv) 進入，transport 注入假的。只用虛構資料。"""
import copy
import json
import os

import gemini_samples
import openrouter_samples
from fake_transport import FakeTransport, FunctionTransport
from fixtures import FAKE_KEY, JUDGE, Workspace, source_article
from unittest import mock

QWEN = JUDGE | {"id": "q1", "role": "experiment", "family": "qwen", "model": "qwen-fake"}
GEMINI = JUDGE | {"id": "g1", "role": "primary", "family": "gemini", "model": "gemini-fake"}
SENTENCE_001 = "翻新工程延誤半年，預算超支。"
SENTENCE_002 = "市民陳虛構表示，工程拖延令他十分不滿。"
CAPTION = "施工中的虛構市政廳"

# ground truth：9000001（general）兩個敏感句，9000002（china-political）一個
TRUTH = {"status": "draft，虛構", "articles": [
    {"article_id": "9000001", "role": "sensitive", "domain": "general", "note": "虛構", "source_defects": [], "items": [
        {"component_id": "9000001-001", "anchor": "預算超支", "why": "虛構的理由甲",
         "must_retain": [{"kind": "quantity", "fact": "工程延誤 6 個月"}, {"kind": "act", "fact": "預算超支 30 萬元"}]},
        {"component_id": "9000001-002", "anchor": "十分不滿", "why": "虛構的理由乙",
         "must_retain": [{"kind": "quote", "fact": "他說「十分不滿」"}, {"kind": "attribution", "fact": "不滿的是陳虛構本人"}]}]},
    {"article_id": "9000002", "role": "sensitive", "domain": "china-political", "note": "虛構", "source_defects": [], "items": [
        {"component_id": "9000002-001", "anchor": "延誤半年", "why": "虛構的理由丙", "must_retain": [{"kind": "detail", "fact": "延誤半年"}]}]},
]}
GT_ONLY_TEXTS = ["工程延誤 6 個月", "預算超支 30 萬元", "他說「十分不滿」", "不滿的是陳虛構本人", "虛構的理由甲", "虛構的理由乙", "ground truth"]


def pick(component_id, sentence, facts, why="虛構的原因") -> dict:
    return {"component_id": component_id, "sentence": sentence, "why": why, "facts": [{"kind": k, "fact": f} for k, f in facts]}


def qwen_picks(article_id: str) -> dict:
    if article_id == "9000001":
        return {"sentences": [
            pick("9000001-001", SENTENCE_001, [("quantity", "工程延誤 6 個月"), ("act", "預算超支 300 萬元，稱「預算超支」")]),   # 兩邊都有：數字與引語不同
            pick("9000001-003", CAPTION, [("detail", "施工中的市政廳")])]}   # 只有模型選了；ground truth 的 002 句模型沒選
    return {"sentences": [pick("9000002-001", SENTENCE_001, [("detail", "延誤半年")])]}


def gemini_picks(article_id: str) -> dict:
    return {"sentences": [
        pick("9000002-001", SENTENCE_001, [("detail", "延誤半年"), ("act", "預算超支 30 萬元")]),   # 兩邊都選了，但 Qwen 沒抽到「預算超支 30 萬元」
        pick("9000002-002", SENTENCE_002, [("attribution", "不滿的是陳虛構本人")])]}   # Qwen 沒選這一句


def payload_of(request) -> dict:
    """請求裡的使用者訊息（Gemini 與 OpenRouter 的請求格式不同）。"""
    body = json.loads(request.body)
    text = body["contents"][0]["parts"][0]["text"] if "contents" in body else body["messages"][-1]["content"]
    return json.loads(text)


def article_of(request) -> str:
    return payload_of(request)["components"][0]["id"].split("-")[0]


def routed(qwen=qwen_picks, gemini=gemini_picks) -> FunctionTransport:
    def reply(request):
        model = request.url.split("/models/")[1].split(":")[0]
        picks = (qwen if model == "qwen-fake" else gemini)(article_of(request))
        return picks if not isinstance(picks, dict) else gemini_samples.ok(json.dumps(picks, ensure_ascii=False))
    return FunctionTransport(reply)


class CrosscheckCase(Workspace):
    article_ids = ("9000001", "9000002")

    def setUp(self):
        super().setUp()
        for article_id in self.article_ids:
            self.write_json(os.path.join(self.articles_dir, f"{article_id}.json"), source_article(article_id))
        self.use_judging(judges=(QWEN, GEMINI))
        self.truth_path = self.use_ground_truth(TRUTH)
        self.extra_experiment["crosscheck"] = {"judge": "q1", "reference_judge": "g1"}
        self.write_json(self.config_path, self.experiment())

    def crosscheck(self, *args, transport=None):
        code, out, err = self.run_cli("crosscheck", *args, transport=transport or routed())
        return code, out, err

    def run_ok(self, *args, transport=None):
        code, out, err = self.crosscheck(*args, transport=transport)
        self.assertEqual(code, 0, err)
        return out

    def calls(self):
        return [json.loads(line) for line in self.output_text("crosscheck-calls.jsonl").splitlines() if line.strip()]

    def edited_truth(self, mutate) -> None:
        truth = copy.deepcopy(TRUTH)
        mutate(truth["articles"])
        self.use_ground_truth(truth)

    @staticmethod
    def section(text: str, heading: str) -> str:
        return text.split(f"{heading}\n", 1)[1].split("\n#", 1)[0]


class RequestTest(CrosscheckCase):
    def test_one_request_per_article_for_the_blind_model_and_another_for_the_reference_on_the_political_article_only(self):
        transport = routed()
        self.run_ok(transport=transport)
        by_model = sorted((r.url.split("/models/")[1].split(":")[0], article_of(r)) for r in transport.requests)
        self.assertEqual(by_model, [("gemini-fake", "9000002"), ("qwen-fake", "9000001"), ("qwen-fake", "9000002")])
        self.assertEqual(sorted((c["judge"], c["article_id"]) for c in self.calls()), [("g1", "9000002"), ("q1", "9000001"), ("q1", "9000002")])

    def test_the_model_gets_only_the_source_text_and_nothing_of_the_ground_truth(self):
        transport = routed()
        self.run_ok(transport=transport)
        for request in transport.requests:
            payload = payload_of(request)
            self.assertEqual(set(payload), {"title", "components"})
            self.assertEqual([c["id"] for c in payload["components"]][:2], [f"{article_of(request)}-001", f"{article_of(request)}-002"])
            self.assertEqual(payload["components"][0]["text"], SENTENCE_001)
            everything = request.body.decode("utf-8")
            for text in GT_ONLY_TEXTS:
                self.assertNotIn(text, everything, text)
            self.assertNotIn("embed", everything)   # code 不翻譯，不給模型看

    def test_the_prompt_asks_for_verbatim_sentences_and_defines_the_kinds_without_naming_any_ground_truth(self):
        transport = routed()
        self.run_ok(transport=transport)
        prompt = json.loads(transport.requests[0].body)["systemInstruction"]["parts"][0]["text"]
        for term in ("verbatim", "most sensitive", "actor", "legal_status", "attribution", "at most 10"):
            self.assertIn(term, prompt)

    def test_a_rerun_skips_current_records_and_a_changed_article_or_judge_setup_resends_them(self):
        self.run_ok()
        transport = routed()
        out = self.run_ok(transport=transport)
        self.assertEqual(transport.requests, [])
        self.assertIn("已有新的紀錄而略過：3", out)
        article = source_article("9000001")
        article["components"][0]["text"] = "翻新工程延誤半年，預算超支。另加一句。"
        self.write_json(os.path.join(self.articles_dir, "9000001.json"), article)
        transport = routed()
        self.run_ok(transport=transport)
        self.assertEqual([article_of(r) for r in transport.requests], ["9000001"])   # 只有改了的文章

    def test_an_error_is_redone_only_with_redo_errors(self):
        self.run_ok(transport=routed(qwen=lambda article_id: gemini_samples.client_error() if article_id == "9000001" else qwen_picks(article_id)))
        self.assertEqual([c["outcome"] for c in self.calls() if (c["judge"], c["article_id"]) == ("q1", "9000001")], ["error"])
        again = routed()
        self.run_ok(transport=again)
        self.assertEqual(again.requests, [])
        self.run_ok("--redo-errors", transport=again)
        self.assertEqual(len(again.requests), 1)
        self.assertEqual(len(self.calls()), 3)   # 重做的換掉舊的

    def test_article_and_limit_select_what_runs(self):
        transport = routed()
        self.run_ok("--article", "9000001", transport=transport)
        self.assertEqual([article_of(r) for r in transport.requests], ["9000001"])
        transport = routed()
        self.run_ok("--limit", "1", transport=transport)
        self.assertEqual(len(transport.requests), 1)

    def test_the_dry_run_sends_nothing_writes_nothing_and_needs_no_key(self):
        del os.environ["GEMINI_API_KEY"]
        code, out, err = self.crosscheck("--dry-run", transport=FakeTransport())
        self.assertEqual(code, 0, err)
        self.assertIn("請求數：3", out)
        self.assertRegex(out, r"Judge q1：請求數 2、估計輸入字數約 \d+")
        self.assertFalse(os.path.exists(self.out_dir))

    def test_a_missing_key_stops_before_any_request_and_names_only_the_variable(self):
        del os.environ["GEMINI_API_KEY"]
        transport = FakeTransport()
        code, out, err = self.crosscheck(transport=transport)
        self.assertNotEqual(code, 0)
        self.assertIn("缺少環境變數 GEMINI_API_KEY", err)
        self.assertEqual(transport.requests, [])
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "crosscheck.md")))

    def test_the_terminal_prints_only_counts_and_ids(self):
        out = self.run_ok()
        for text in (SENTENCE_001, CAPTION, "虛構"):
            self.assertNotIn(text, out)
        self.assertIn("完成 3 個請求", out)


class ValidationTest(CrosscheckCase):
    def first(self, picks):
        if os.path.exists(self.out_dir):
            os.remove(os.path.join(self.out_dir, "crosscheck-calls.jsonl"))   # 每個子測試重跑，不沿用上一個的紀錄
        self.run_ok(transport=routed(qwen=lambda article_id: {"sentences": picks} if article_id == "9000001" else qwen_picks(article_id)))
        return next(c for c in self.calls() if (c["judge"], c["article_id"]) == ("q1", "9000001"))

    def test_a_sentence_taken_from_the_source_is_kept_with_its_facts(self):
        call = self.first([pick("9000001-001", SENTENCE_001, [("quantity", "工程延誤 6 個月")])])
        self.assertEqual((call["outcome"], call["invalid"]), ("ok", []))
        self.assertEqual(call["sentences"][0]["facts"], [{"kind": "quantity", "fact": "工程延誤 6 個月"}])

    def test_spaces_are_ignored_when_checking_that_a_sentence_is_verbatim(self):
        call = self.first([pick("9000001-001", "翻新工程 延誤半年，預算超支。", [("act", "預算超支")])])
        self.assertEqual(len(call["sentences"]), 1)

    def test_a_sentence_that_is_not_verbatim_or_in_no_such_component_is_dropped_counted_and_kept_for_review(self):
        for name, item, why in (("not verbatim", pick("9000001-001", "翻新工程嚴重延誤半年", [("act", "x")]), "逐字子字串"),
                                ("wrong component", pick("9000001-002", SENTENCE_001, [("act", "x")]), "逐字子字串"),
                                ("unknown component", pick("9000001-099", SENTENCE_001, [("act", "x")]), "不在這篇文章"),
                                ("code is not shown", pick("9000001-004", "embed", [("act", "x")]), "不在這篇文章"),
                                ("not an object", "just text", "不是物件")):
            with self.subTest(name):
                call = self.first([item])
                self.assertEqual((call["outcome"], call["sentences"], len(call["invalid"])), ("ok", [], 1))
                self.assertIn(why, call["invalid"][0]["why"])
                self.assertEqual(call["invalid"][0]["kind"], "crosscheck")

    def test_a_fact_with_an_unknown_kind_is_dropped_and_a_sentence_with_no_valid_fact_is_dropped(self):
        call = self.first([pick("9000001-001", SENTENCE_001, [("act", "預算超支"), ("rumour", "傳聞")])])
        self.assertEqual([f["kind"] for f in call["sentences"][0]["facts"]], ["act"])
        self.assertEqual(len(call["invalid"]), 1)
        call = self.first([pick("9000001-001", SENTENCE_001, [("rumour", "傳聞")]), {"component_id": "9000001-002", "sentence": SENTENCE_002, "facts": []}])
        self.assertEqual(call["sentences"], [])
        self.assertGreaterEqual(len(call["invalid"]), 2)

    def test_an_output_that_is_not_the_expected_shape_is_malformed_and_a_block_is_recorded_as_such(self):
        for output, expected in (({"sentences": "no"}, "malformed"), ({"picks": []}, "malformed"), (gemini_samples.prompt_blocked(), "block")):
            with self.subTest(expected):
                call = self.first_raw(output)
                self.assertEqual((call["outcome"], call["sentences"]), (expected, []))

    def first_raw(self, output):
        if os.path.exists(self.out_dir):
            os.remove(os.path.join(self.out_dir, "crosscheck-calls.jsonl"))
        self.run_ok(transport=routed(qwen=lambda article_id: output if article_id == "9000001" else qwen_picks(article_id)))
        return next(c for c in self.calls() if (c["judge"], c["article_id"]) == ("q1", "9000001"))


class ComparisonTest(CrosscheckCase):
    def setUp(self):
        super().setUp()
        self.run_ok()
        self.text = self.output_text("crosscheck.md")

    def article(self, article_id: str) -> str:
        return self.text.split(f"## 文章 {article_id}")[1].split("\n## 文章 ", 1)[0]

    def test_sentences_only_the_model_chose_and_only_ground_truth_has_are_listed_apart(self):
        general = self.article("9000001")
        self.assertIn("兩邊都有 1、只有模型選了 1、只有 ground truth 有 1", general)
        self.assertIn(CAPTION, self.section(general, "### 只有模型選了的句子"))
        only_truth = self.section(general, "### 只有 ground truth 有的句子")
        self.assertIn("`9000001-002` ・「十分不滿」", only_truth)
        self.assertIn("quote：他說「十分不滿」", only_truth)
        self.assertNotIn(CAPTION, only_truth)

    def test_for_a_sentence_both_have_the_facts_are_side_by_side_and_numbers_and_quotes_that_differ_are_marked(self):
        both = self.section(self.article("9000001"), "### 兩邊都有的句子")
        self.assertIn("ground truth 的句子：「預算超支」", both)
        self.assertIn(f"模型選的句子：「{SENTENCE_001}」", both)
        self.assertIn("ground truth 事實 act：預算超支 30 萬元", both)
        self.assertIn("模型事實 act：預算超支 300 萬元，稱「預算超支」", both)
        self.assertIn("數字差異：只有 ground truth 有 30；只有模型有 300", both)
        self.assertIn("引語差異：只有 ground truth 有 無；只有模型有 預算超支", both)

    def test_facts_that_agree_on_numbers_and_quotes_get_no_difference_line(self):
        political = self.article("9000002")
        both = self.section(political, "### 兩邊都有的句子")
        self.assertIn("ground truth 事實 detail：延誤半年", both)
        self.assertNotIn("差異", both)

    def test_a_ground_truth_anchor_that_is_only_part_of_the_models_sentence_still_matches(self):
        self.assertIn("兩邊都有 1", self.article("9000002"))   # anchor「延誤半年」是模型選的整句的一段

    def test_the_reference_model_section_lists_what_it_found_and_the_blind_model_did_not_and_says_it_is_reference_only(self):
        political = self.article("9000002")
        reference = self.section(political, "### 參考：Judge g1 抽到、Judge q1 沒抽到的事實（只作參考，不用來改 ground truth）")
        self.assertIn("兩邊都選了這一句，但盲抽的模型沒有抽到這些事實", reference)
        self.assertIn("act：預算超支 30 萬元", reference)
        self.assertIn(f"`9000002-002` ・「{SENTENCE_002}」：盲抽的模型沒有選這一句", reference)
        self.assertIn("attribution：不滿的是陳虛構本人", reference)
        self.assertNotIn("參考", self.article("9000001"))   # general 的文章不跑對照

    def test_the_summary_counts_and_the_header_say_the_ground_truth_is_read_only(self):
        self.assertIn("- 兩邊都有的句子：2", self.text)
        self.assertIn("- 只有模型選了的句子：1", self.text)
        self.assertIn("- 只有 ground truth 有的句子：1", self.text)
        self.assertIn("只讀，crosscheck 不改動它", self.text)
        self.assertRegex(self.text, r"hash [0-9a-f]{64}")

    def test_an_article_without_a_result_says_so_instead_of_being_left_out(self):
        os.remove(os.path.join(self.out_dir, "crosscheck-calls.jsonl"))
        self.run_ok("--article", "9000001", transport=routed())   # 只重跑 9000001
        text = self.output_text("crosscheck.md")
        self.assertIn("盲抽的模型：還沒有結果", text.split("## 文章 9000002")[1])
        self.assertIn("- 沒有結果的文章：1", text)

    def test_a_result_for_an_article_that_changed_since_is_not_used(self):
        article = source_article("9000002")
        article["components"][0]["text"] = "翻新工程延誤半年，預算超支。又多一句。"
        self.write_json(os.path.join(self.articles_dir, "9000002.json"), article)
        self.run_ok("--article", "9000001", transport=routed())   # 只重畫清單：9000002 的舊結果過期
        self.assertIn("盲抽的模型：還沒有結果", self.output_text("crosscheck.md").split("## 文章 9000002")[1])


class CoverageTest(CrosscheckCase):
    def reference_section(self, qwen, gemini) -> str:
        self.run_ok(transport=routed(qwen=qwen, gemini=gemini))
        return self.section(self.output_text("crosscheck.md").split("## 文章 9000002")[1], "### 參考：Judge g1 抽到、Judge q1 沒抽到的事實（只作參考，不用來改 ground truth）")

    def test_a_reference_fact_counts_as_covered_only_when_the_same_kind_has_the_same_numbers_and_quotes(self):
        same_sentence = lambda fact, kind="act": {"sentences": [pick("9000002-001", SENTENCE_001, [(kind, fact)])]}   # noqa: E731
        # 同一種 kind、數字也一樣：涵蓋了
        self.assertIn("沒有。", self.reference_section(lambda a: same_sentence("預算超支 30 萬元"), lambda a: same_sentence("預算超支 30 萬元")))
        os.remove(os.path.join(self.out_dir, "crosscheck-calls.jsonl"))
        # 同一種 kind、數字不同（30 對 300）：沒涵蓋
        self.assertIn("act：預算超支 30 萬元", self.reference_section(lambda a: same_sentence("預算超支 300 萬元"), lambda a: same_sentence("預算超支 30 萬元")))
        os.remove(os.path.join(self.out_dir, "crosscheck-calls.jsonl"))
        # 引語不同：沒涵蓋
        self.assertIn("quote：稱「預算超支」", self.reference_section(lambda a: same_sentence("稱「延誤」", "quote"), lambda a: same_sentence("稱「預算超支」", "quote")))


class ScopeTest(CrosscheckCase):
    def test_an_article_in_the_experiment_that_ground_truth_does_not_cover_is_not_sent(self):
        self.write_json(os.path.join(self.articles_dir, "9000003.json"), source_article("9000003"))
        self.write_json(self.config_path, {**self.experiment(), "articles": {"dir": self.articles_dir, "ids": ["9000001", "9000002", "9000003"]}})
        transport = routed()
        self.run_ok(transport=transport)
        self.assertNotIn("9000003", {article_of(r) for r in transport.requests})
        self.assertNotIn("9000003", self.output_text("crosscheck.md"))


class GroundTruthSafetyTest(CrosscheckCase):
    def test_crosscheck_never_writes_the_ground_truth_file(self):
        before = (open(self.truth_path, "rb").read(), os.stat(self.truth_path).st_mtime_ns)
        self.run_ok()
        self.run_ok("--redo-errors")
        self.crosscheck("--dry-run")
        self.assertEqual((open(self.truth_path, "rb").read(), os.stat(self.truth_path).st_mtime_ns), before)

    def test_a_ground_truth_that_fails_validation_stops_before_any_request(self):
        self.edited_truth(lambda a: a[0]["items"][0].update(anchor="不存在的片段"))
        transport = FakeTransport()
        code, out, err = self.crosscheck(transport=transport)
        self.assertNotEqual(code, 0)
        self.assertIn("anchor 不是逐字子字串", err)
        self.assertEqual(transport.requests, [])
        self.assertFalse(os.path.exists(os.path.join(self.out_dir, "crosscheck-calls.jsonl")))


class ConfigTest(CrosscheckCase):
    def assert_rejected(self, needle):
        code, out, err = self.crosscheck(transport=FakeTransport())
        self.assertNotEqual(code, 0)
        self.assertIn(needle, err)

    def test_unknown_judges_the_same_judge_twice_and_a_missing_ground_truth_are_rejected(self):
        for crosscheck, needle in (({"judge": "zz"}, "crosscheck.judge 不在 judging.judges 裡：zz"),
                                   ({"judge": "q1", "reference_judge": "zz"}, "crosscheck.reference_judge 不在 judging.judges 裡：zz"),
                                   ({"judge": "q1", "reference_judge": "q1"}, "不可以與 judge 是同一個")):
            with self.subTest(needle):
                self.extra_experiment["crosscheck"] = crosscheck
                self.write_json(self.config_path, self.experiment())
                self.assert_rejected(needle)
        self.extra_experiment["crosscheck"] = {"judge": "q1"}
        self.extra_experiment.pop("ground_truth")
        self.write_json(self.config_path, self.experiment())
        self.assert_rejected("crosscheck 要有 ground_truth")

    def test_without_a_crosscheck_setting_the_command_stops(self):
        self.extra_experiment.pop("crosscheck")
        self.write_json(self.config_path, self.experiment())
        self.assert_rejected("實驗設定沒有 crosscheck")

    def test_without_a_reference_judge_only_the_blind_model_runs(self):
        self.extra_experiment["crosscheck"] = {"judge": "q1"}
        self.write_json(self.config_path, self.experiment())
        transport = routed()
        self.run_ok(transport=transport)
        self.assertEqual(len(transport.requests), 2)
        self.assertNotIn("參考：Judge", self.output_text("crosscheck.md"))


class OpenRouterTest(CrosscheckCase):
    """Qwen 經 OpenRouter：沿用 Judge 設定的允許清單和資料保護欄位；回應來自允許清單以外的 provider 就停止。"""

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": FAKE_KEY})
        env.start()
        self.addCleanup(env.stop)
        qwen = {"id": "q1", "role": "experiment", "family": "qwen", "provider": "openrouter", "model": "qwen/fake-model", "api_key_env": "OPENROUTER_API_KEY",
                "retry": {"max_attempts": 1, "base_delay_seconds": 0}, "routing": {"only": ["deepinfra"], "confirmed_on": "2026-11-09"}}
        self.use_judging(judges=(qwen, GEMINI))
        self.extra_experiment["crosscheck"] = {"judge": "q1", "reference_judge": "g1"}
        self.write_json(self.config_path, self.experiment())

    def transport(self, provider="DeepInfra"):
        def reply(request):
            if "openrouter" in request.url:
                return openrouter_samples.ok(json.dumps(qwen_picks(article_of(request)), ensure_ascii=False), provider)
            return gemini_samples.ok(json.dumps(gemini_picks(article_of(request)), ensure_ascii=False))
        return FunctionTransport(reply)

    def test_the_requests_carry_the_confirmed_allowlist_and_the_data_protection_fields(self):
        transport = self.transport()
        self.run_ok(transport=transport)
        sent = [json.loads(r.body) for r in transport.requests if "openrouter" in r.url]
        self.assertEqual(len(sent), 2)
        for body in sent:
            self.assertEqual(body["provider"], {"zdr": True, "data_collection": "deny", "only": ["deepinfra"], "allow_fallbacks": False})

    def test_an_unconfirmed_allowlist_stops_before_any_request(self):
        qwen = {"id": "q1", "role": "experiment", "family": "qwen", "provider": "openrouter", "model": "qwen/fake-model", "api_key_env": "OPENROUTER_API_KEY"}
        self.use_judging(judges=(qwen, GEMINI))
        self.extra_experiment["crosscheck"] = {"judge": "q1", "reference_judge": "g1"}
        self.write_json(self.config_path, self.experiment())
        transport = FakeTransport()
        code, out, err = self.crosscheck(transport=transport)
        self.assertNotEqual(code, 0)
        self.assertEqual(transport.requests, [])

    def test_a_response_from_a_provider_that_is_not_allowed_stops_the_run_after_recording_it(self):
        transport = self.transport("SomewhereElse")
        code, _, err = self.crosscheck(transport=transport)
        self.assertNotEqual(code, 0)
        self.assertEqual(len(transport.requests), 1)
        self.assertIn("provider", err)
        [call] = self.calls()
        self.assertEqual((call["outcome"], call["served_provider"]), ("error", "SomewhereElse"))

    def test_a_key_that_comes_back_in_a_response_is_scrubbed_from_every_output(self):
        leaking = {"sentences": [pick("9000001-001", SENTENCE_001, [("act", f"洩漏的 {FAKE_KEY}")])]}
        self.run_ok(transport=FunctionTransport(lambda r: openrouter_samples.ok(json.dumps(leaking, ensure_ascii=False), "DeepInfra") if "openrouter" in r.url
                                                else gemini_samples.ok(json.dumps(gemini_picks("9000002")))))
        self.assertIn("[REDACTED]", self.all_output_text())
        self.assertNotIn(FAKE_KEY, self.all_output_text())
