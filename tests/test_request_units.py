"""一個 Run 送出多個請求：切塊（article-json 加上輸入上限）與逐 Component 模式。結果取最差的一個，並記下是哪一個。"""
import json
import os
import shutil
import unittest
from unittest import mock

import gemini_samples
import openrouter_samples
from fake_transport import FunctionTransport
from fixtures import APPROACH, FAKE_KEY, Workspace

ID = "9000001"
PARAGRAPH_TEXTS = [f"第{n}段虛構的內容，" + "文字" * 30 for n in range(1, 7)]


def article():
    return {"title": "虛構標題", "mainChannel": "社會新聞", "tags": ["虛構", "測試"], "components": [
        {"id": f"{ID}-{n:03d}", "type": "paragraph", "text": text} for n, text in enumerate(PARAGRAPH_TEXTS, 1)],
        "meta": {"source": "public-web", "articleId": int(ID), "unknown": [], "warnings": []}}


def mixed_article():
    return {"title": "虛構標題", "mainChannel": "社會新聞", "tags": ["虛構", "測試"], "components": [
        {"id": f"{ID}-001", "type": "highlight", "text": "翻新工程延誤半年。"},
        {"id": f"{ID}-002", "type": "heading", "level": "h2", "text": "市民反應"},
        {"id": f"{ID}-003", "type": "paragraph", "text": "市民陳虛構表示不滿。"},
        {"id": f"{ID}-004", "type": "quote", "text": "「虛構引語」", "source": "虛構人物"},
        {"id": f"{ID}-005", "type": "image", "caption": "施工中的虛構市政廳"},
        {"id": f"{ID}-006", "type": "gallery", "captions": ["圖一", "", "圖三"]},
        {"id": f"{ID}-007", "type": "related-article", "titles": ["相關一", "相關二"]},
        {"id": f"{ID}-008", "type": "code", "code": "<div>embed</div>"},
        {"id": f"{ID}-009", "type": "video"},
        {"id": f"{ID}-010", "type": "paragraph", "text": "   "}],
        "meta": {"source": "public-web", "articleId": int(ID), "unknown": [], "warnings": []}}


def user_message(request):
    return json.loads(request.body)["contents"][0]["parts"][0]["text"]


def system_message(request):
    return json.loads(request.body)["systemInstruction"]["parts"][0]["text"]


def translate_json_chunk(request):
    """假的翻譯：把送來的 JSON 的每一段文字前面加 EN:。"""
    payload = json.loads(user_message(request))
    translated = {"title": "EN:" + payload["title"] if payload["title"] else "", "tags": ["EN:" + t for t in payload["tags"]],
                  "components": [{**c, "text": "EN:" + c["text"]} for c in payload["components"]]}
    return gemini_samples.ok(json.dumps(translated, ensure_ascii=False))


class UnitCase(Workspace):
    source = staticmethod(article)
    approach_changes: dict = {}

    def setUp(self):
        super().setUp()
        self.write_json(os.path.join(self.articles_dir, f"{ID}.json"), self.source())
        self.write_json(self.approach_path, {**APPROACH, **self.approach_changes})

    def translate(self, reply, *args):
        shutil.rmtree(self.data_dir, ignore_errors=True)
        transport = FunctionTransport(reply)
        code, out, err = self.run_cli("translate", *args, transport=transport)
        return code, transport, out, err

    def record(self):
        [record] = self.read_runs()
        return record

    def translation(self):
        with open(os.path.join(self.out_dir, self.record()["translation"]), encoding="utf-8") as f:
            return json.load(f)


def payload_length(components, with_title=True):
    return len(json.dumps({"title": "虛構標題" if with_title else "", "tags": ["虛構", "測試"] if with_title else [],
                           "components": components}, ensure_ascii=False))


class ChunkingTest(UnitCase):
    one_component = payload_length(article()["components"][:1]) + 5   # 放得下標題、tags 和一個 Component，放不下兩個
    approach_changes = {"max_input_chars": one_component}

    def test_components_are_split_in_order_and_every_one_is_sent_exactly_once(self):
        code, transport, _, err = self.translate(translate_json_chunk)
        self.assertEqual(code, 0, err)
        payloads = [json.loads(user_message(r)) for r in transport.requests]
        self.assertEqual(len(payloads), 6)   # 每塊只放得下一個 Component
        self.assertEqual([c["id"] for p in payloads for c in p["components"]], [c["id"] for c in article()["components"]])
        self.assertTrue(all(len(user_message(r)) <= self.one_component for r in transport.requests))

    def test_the_title_and_tags_are_only_in_the_first_chunk(self):
        _, transport, _, _ = self.translate(translate_json_chunk)
        payloads = [json.loads(user_message(r)) for r in transport.requests]
        self.assertEqual((payloads[0]["title"], payloads[0]["tags"]), ("虛構標題", ["虛構", "測試"]))
        for later in payloads[1:]:
            self.assertEqual((later["title"], later["tags"]), ("", []))

    def test_a_larger_limit_gives_fewer_chunks_and_a_huge_one_gives_a_single_request(self):
        for limit, expected in ((payload_length(article()["components"][:3]) + 5, 2), (100000, 1)):
            with self.subTest(limit):
                self.write_json(self.approach_path, {**APPROACH, "max_input_chars": limit})
                _, transport, _, _ = self.translate(translate_json_chunk)
                self.assertEqual(len(transport.requests), expected)

    def test_a_component_bigger_than_the_limit_is_sent_alone_not_split_and_not_dropped(self):
        self.write_json(self.approach_path, {**APPROACH, "max_input_chars": 50})
        _, transport, _, _ = self.translate(translate_json_chunk)
        payloads = [json.loads(user_message(r)) for r in transport.requests]
        self.assertEqual([[c["id"] for c in p["components"]] for p in payloads], [[c["id"]] for c in article()["components"]])

    def test_the_translations_are_put_back_together_in_the_original_structure(self):
        self.translate(translate_json_chunk)
        translation = self.translation()
        self.assertEqual((translation["title"], translation["tags"]), ("EN:虛構標題", ["EN:虛構", "EN:測試"]))
        self.assertEqual([(c["id"], c["type"], c["text"]) for c in translation["components"]],
                         [(c["id"], "paragraph", "EN:" + c["text"]) for c in article()["components"]])
        self.assertEqual(self.record()["outcome"], "ok")
        self.assertEqual(self.run_cli("check")[0], 0)   # 組回來的譯文與沒有切塊的一樣，check 看不出差別

    def test_it_is_one_run_record_with_summed_tokens_and_every_attempt_and_the_raw_responses(self):
        self.translate(translate_json_chunk)
        record = self.record()
        self.assertEqual((record["input_tokens"], record["output_tokens"], record["thought_tokens"]), (720, 480, 180))   # 6 個請求，各 120／80／30
        self.assertEqual([a["unit"] for a in record["attempts"]], [f"chunk {n}/6" for n in range(1, 7)])
        self.assertIsNone(record["worst_unit"])
        self.assertTrue(record["raw_response"].endswith(".jsonl"))
        with open(os.path.join(self.out_dir, record["raw_response"]), encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
        self.assertEqual([line["unit"] for line in lines], [f"chunk {n}/6" for n in range(1, 7)])
        self.assertIn("candidates", json.loads(lines[0]["body"]))

    def test_a_blocked_chunk_makes_the_run_a_block_and_the_rest_is_not_sent(self):
        def reply(request):
            return gemini_samples.prompt_blocked() if "第3段" in user_message(request) else translate_json_chunk(request)
        _, transport, _, _ = self.translate(reply)
        record = self.record()
        self.assertEqual((record["outcome"], record["reason"], record["worst_unit"]), ("block", "prompt:PROHIBITED_CONTENT", "chunk 3/6"))
        self.assertEqual(len(transport.requests), 3)
        self.assertIsNone(record["translation"])

    def test_the_worst_chunk_decides_in_the_order_block_refusal_malformed_truncated_error_ok(self):
        samples = {
            "block": gemini_samples.prompt_blocked(), "refusal": gemini_samples.refusal(), "malformed": gemini_samples.prose_not_refusal(),
            "truncated": gemini_samples.max_tokens('{"title": "Fict'), "error": gemini_samples.unknown_format(), "ok": None,
        }
        order = ["block", "refusal", "malformed", "truncated", "error", "ok"]
        for worse_index, worse in enumerate(order[:-1]):
            for better in order[worse_index + 1:]:
                for first_is_worse in (True, False):
                    with self.subTest(worse=worse, better=better, first_is_worse=first_is_worse):
                        wanted = {1: worse, 4: better} if first_is_worse else {1: better, 4: worse}
                        def reply(request):
                            for number, kind in wanted.items():
                                if f"第{number}段" in user_message(request) and samples[kind] is not None:
                                    return samples[kind]
                            return translate_json_chunk(request)
                        self.translate(reply)
                        record = self.record()
                        self.assertEqual(record["outcome"], worse)
                        self.assertEqual(record["worst_unit"], "chunk 1/6" if first_is_worse else "chunk 4/6")

    def test_the_first_of_two_equally_bad_chunks_is_the_one_recorded(self):
        def reply(request):
            return gemini_samples.unknown_format() if any(f"第{n}段" in user_message(request) for n in (2, 5)) else translate_json_chunk(request)
        self.translate(reply)
        self.assertEqual((self.record()["outcome"], self.record()["worst_unit"]), ("error", "chunk 2/6"))

    def test_a_chunk_is_retried_on_its_own_and_its_attempts_are_labelled(self):
        state = {"failed": False}
        def reply(request):
            if "第2段" in user_message(request) and not state["failed"]:
                state["failed"] = True
                return gemini_samples.rate_limited()
            return translate_json_chunk(request)
        self.translate(reply)
        record = self.record()
        self.assertEqual(record["outcome"], "ok")
        self.assertEqual([(a["unit"], a["http_status"]) for a in record["attempts"][:3]], [("chunk 1/6", 200), ("chunk 2/6", 429), ("chunk 2/6", 200)])

    def test_the_term_list_entries_are_injected_into_the_chunk_that_contains_them(self):
        self.use_term_list({"terms": [{"source": "第4段", "category": "place", "translations": {"trad-to-en": ["Section Four"]}}]})
        _, transport, _, _ = self.translate(translate_json_chunk)
        with_term = [i for i, r in enumerate(transport.requests) if "Section Four" in system_message(r)]
        self.assertEqual(with_term, [3])

    def test_the_dry_run_counts_one_run_and_one_request_per_chunk(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)
        transport = FunctionTransport(translate_json_chunk)
        code, out, _ = self.run_cli("translate", "--dry-run", transport=transport)
        self.assertEqual(code, 0)
        self.assertIn("Run：1", out)
        self.assertIn("請求數：6", out)
        self.assertEqual(transport.requests, [])

    def test_a_translation_is_not_kept_when_any_chunk_failed(self):
        self.translate(lambda r: gemini_samples.unknown_format() if "第6段" in user_message(r) else translate_json_chunk(r))
        self.assertIsNone(self.record()["translation"])


class ConfigTest(UnitCase):
    def test_invalid_settings_are_rejected(self):
        for name, changes, needle in (
            ("limit of zero", {"max_input_chars": 0}, "max_input_chars"),
            ("negative limit", {"max_input_chars": -5}, "max_input_chars"),
            ("limit with per-component", {"request_mode": "per-component", "max_input_chars": 500}, "max_input_chars"),
            ("unknown mode", {"request_mode": "per-sentence"}, "request_mode"),
        ):
            with self.subTest(name):
                self.write_json(self.approach_path, {**APPROACH, **changes})
                self.assert_fails_cleanly(needle)


class PerComponentTest(UnitCase):
    source = staticmethod(mixed_article)
    approach_changes = {"request_mode": "per-component"}

    @staticmethod
    def echo(request):
        return gemini_samples.ok("EN:" + user_message(request))

    def test_one_request_per_translatable_string_and_none_for_embedded_or_blank_ones(self):
        _, transport, _, err = self.translate(self.echo)
        sent = [user_message(r) for r in transport.requests]
        self.assertEqual(sent, ["虛構標題", "虛構", "測試", "翻新工程延誤半年。", "市民反應", "市民陳虛構表示不滿。", "「虛構引語」", "虛構人物",
                                "施工中的虛構市政廳", "圖一", "圖三", "相關一", "相關二"])   # code、video、空白的說明和段落都不送
        self.assertEqual(self.record()["outcome"], "ok", err)

    def test_the_request_is_plain_text_in_and_plain_text_out_not_json(self):
        _, transport, _, _ = self.translate(self.echo)
        body = json.loads(transport.requests[0].body)
        self.assertNotIn("responseMimeType", body["generationConfig"])
        self.assertIn("translation only", system_message(transport.requests[0]))
        self.assertEqual(user_message(transport.requests[0]), "虛構標題")

    def test_the_translations_are_put_back_into_the_same_structure(self):
        self.translate(self.echo)
        translation = self.translation()
        self.assertEqual((translation["title"], translation["tags"]), ("EN:虛構標題", ["EN:虛構", "EN:測試"]))
        by_id = {c["id"]: c for c in translation["components"]}
        self.assertEqual([c["id"] for c in translation["components"]], [c["id"] for c in mixed_article()["components"]])
        self.assertEqual(by_id[f"{ID}-002"], {"id": f"{ID}-002", "type": "heading", "level": "h2", "text": "EN:市民反應"})   # level 原樣
        self.assertEqual((by_id[f"{ID}-004"]["text"], by_id[f"{ID}-004"]["source"]), ("EN:「虛構引語」", "EN:虛構人物"))
        self.assertEqual(by_id[f"{ID}-006"]["captions"], ["EN:圖一", "", "EN:圖三"])   # 空的那則原樣，位置不變
        self.assertEqual(by_id[f"{ID}-007"]["titles"], ["EN:相關一", "EN:相關二"])
        for embedded in (f"{ID}-008", f"{ID}-009"):   # 不翻譯的 Component 原樣
            self.assertEqual(by_id[embedded], next(c for c in mixed_article()["components"] if c["id"] == embedded))
        self.assertEqual(by_id[f"{ID}-010"]["text"], "   ")

    def test_the_result_goes_through_check_like_any_other_translation(self):
        self.translate(self.echo)
        self.assertEqual(self.run_cli("check")[0], 0)
        with open(os.path.join(self.out_dir, "components.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual([f["subtype"] for r in rows for f in r["findings"] if f["subtype"] == "omission"], [])

    def test_the_run_record_names_the_component_prompt(self):
        self.translate(self.echo)
        self.assertEqual(self.record()["prompt_id"], "translate-trad-to-en-component")

    def test_output_is_stripped_and_an_empty_output_is_malformed_at_that_unit(self):
        self.translate(lambda r: gemini_samples.ok("  EN:" + user_message(r) + "\n"))
        self.assertEqual(self.translation()["title"], "EN:虛構標題")
        self.translate(lambda r: gemini_samples.ok("   ") if user_message(r) == "市民反應" else self.echo(r))
        record = self.record()
        self.assertEqual((record["outcome"], record["worst_unit"]), ("malformed", f"{ID}-002.text"))

    def test_refusal_wording_in_a_translation_is_not_by_itself_a_refusal(self):
        # 譯文的引語裡本來就可能有「I can't」；這個模式只認 provider 明確給的訊號。疑似 Refusal 由 check 的警示攔住
        self.translate(lambda r: gemini_samples.ok("I'm sorry, but I can't help with that.") if user_message(r) == "虛構人物" else self.echo(r))
        self.assertEqual(self.record()["outcome"], "ok")

    def test_a_blocked_unit_makes_the_run_a_block_and_stops_the_rest(self):
        _, transport, _, _ = self.translate(lambda r: gemini_samples.prompt_blocked() if user_message(r) == "市民反應" else self.echo(r))
        record = self.record()
        self.assertEqual((record["outcome"], record["worst_unit"]), ("block", f"{ID}-002.text"))
        self.assertEqual(len(transport.requests), 5)
        self.assertIsNone(record["translation"])

    def test_labels_name_the_component_field_and_list_position(self):
        _, _, _, _ = self.translate(lambda r: gemini_samples.unknown_format() if user_message(r) == "圖三" else self.echo(r))
        self.assertEqual(self.record()["worst_unit"], f"{ID}-006.captions[3]")

    def test_the_main_only_variant_sends_only_the_title_and_the_main_components(self):
        self.rewrite_experiment(lambda e: e["approaches"][0].update(input_variants=["main-only"]))
        _, transport, _, _ = self.translate(self.echo)
        self.assertEqual([user_message(r) for r in transport.requests],
                         ["虛構標題", "翻新工程延誤半年。", "市民反應", "市民陳虛構表示不滿。", "「虛構引語」", "虛構人物"])

    def test_the_term_list_entries_are_injected_into_the_unit_that_contains_them(self):
        self.use_term_list({"terms": [{"source": "陳虛構", "category": "person", "translations": {"trad-to-en": ["Chan Fictional"]}}]})
        _, transport, _, _ = self.translate(self.echo)
        with_term = [user_message(r) for r in transport.requests if "Chan Fictional" in system_message(r)]
        self.assertEqual(with_term, ["市民陳虛構表示不滿。"])

    def test_an_article_with_nothing_to_translate_is_rejected_before_any_request(self):
        nothing = {"title": "", "mainChannel": "社會新聞", "tags": [], "components": [{"id": f"{ID}-001", "type": "code", "code": "<div>embed</div>"}],
                   "meta": {"source": "public-web", "unknown": [], "warnings": []}}
        self.write_json(os.path.join(self.articles_dir, f"{ID}.json"), nothing)
        shutil.rmtree(self.data_dir, ignore_errors=True)
        transport = FunctionTransport(self.echo)
        code, out, err = self.run_cli("translate", transport=transport)
        self.assertNotEqual(code, 0)
        self.assertIn(ID, err)
        self.assertEqual((out, transport.requests), ("", []))
        self.assertFalse(os.path.exists(self.data_dir))

    def test_the_dry_run_counts_one_request_per_string(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)
        code, out, _ = self.run_cli("translate", "--dry-run", transport=FunctionTransport(self.echo))
        self.assertEqual(code, 0)
        self.assertIn("Run：1", out)
        self.assertIn("請求數：13", out)

    def test_trad_to_simplified_has_a_component_prompt_too(self):
        self.directions = ["trad-to-simplified"]
        self.write_json(self.config_path, self.experiment())
        _, transport, _, _ = self.translate(self.echo)
        self.assertIn("Simplified Chinese", system_message(transport.requests[0]))
        self.assertEqual(self.record()["prompt_id"], "translate-trad-to-simplified-component")


class OpenRouterPerComponentTest(Workspace):
    """每個請求都要帶資料保護欄位：逐 Component 模式有好幾個請求，每一個都檢查。"""

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": FAKE_KEY})
        env.start()
        self.addCleanup(env.stop)
        self.write_json(os.path.join(self.articles_dir, f"{ID}.json"), mixed_article())
        self.write_json(self.approach_path, {"id": "f", "family": "hunyuan", "provider": "openrouter", "model": "tencent/hy-mt2",
                                             "api_key_env": "OPENROUTER_API_KEY", "request_mode": "per-component",
                                             "retry": {"max_attempts": 2, "base_delay_seconds": 0},
                                             "routing": {"only": ["deepinfra", "novita"], "confirmed_on": "2026-11-09"}})

    def test_every_request_has_the_protection_fields_and_asks_for_plain_text(self):
        providers = iter(["DeepInfra", "Novita"] * 20)
        transport = FunctionTransport(lambda r: openrouter_samples.ok("EN:" + json.loads(r.body)["messages"][1]["content"], next(providers)))
        code, _, err = self.run_cli("translate", transport=transport)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(transport.requests), 13)
        for request in transport.requests:
            body = json.loads(request.body)
            self.assertEqual(body["provider"], {"zdr": True, "data_collection": "deny", "only": ["deepinfra", "novita"], "allow_fallbacks": False})
            self.assertNotIn("response_format", body)
        self.assertEqual(self.read_runs()[0]["served_provider"], "DeepInfra, Novita")

    def test_an_unlisted_provider_wins_over_a_worse_result_and_stops_the_run_at_once(self):
        calls = []
        def reply(request):
            calls.append(1)
            if len(calls) == 2:   # 比 error 更差的結果，出現在違規之前
                return openrouter_samples.provider_refusal()
            if len(calls) == 4:
                return openrouter_samples.ok("EN:x", "SomewhereElse")
            return openrouter_samples.ok("EN:x", "DeepInfra")
        transport = FunctionTransport(reply)
        self.run_cli("translate", transport=transport)
        record = self.read_runs()[0]
        self.assertEqual(len(transport.requests), 4)   # 資料已經送到不該去的地方：後面的請求不再送
        self.assertEqual((record["outcome"], record["reason"]), ("error", "provider 不在允許清單內"))
        self.assertEqual(record["served_provider"], "DeepInfra, SomewhereElse")

    def test_one_unit_served_by_an_unlisted_provider_makes_the_run_an_error(self):
        calls = {"n": 0}
        def reply(request):
            calls["n"] += 1
            return openrouter_samples.ok("EN:x", "SomewhereElse" if calls["n"] == 3 else "DeepInfra")
        code, _, _ = self.run_cli("translate", transport=FunctionTransport(reply))
        record = self.read_runs()[0]
        self.assertEqual((record["outcome"], record["reason"]), ("error", "provider 不在允許清單內"))
        self.assertIsNone(record["translation"])


if __name__ == "__main__":
    unittest.main()
