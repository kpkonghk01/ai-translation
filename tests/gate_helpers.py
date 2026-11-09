"""Gate、審閱與全文篩查的測試共用：測試用的 Workspace（FactCase、ReviewCase）、虛構的 ground truth、譯文的改動和假 Judge 的路由。只用虛構資料。"""
import copy
import json
import os
import re
from pathlib import Path

import gemini_samples
from fake_transport import FakeTransport, FunctionTransport
from fixtures import GROUND_TRUTH, Workspace, ok_response
from judge_helpers import fact_output, fact_transport, finding, judge_transport, screen_transport, user_payload


class FactCase(Workspace):
    def setUp(self):
        super().setUp()
        self.run_cli("translate", transport=FakeTransport(ok_response()))
        self.use_judging()
        self.use_ground_truth()

    def facts(self, transport, *args):
        return self.run_cli("judge", "--task", "fact", *args, transport=transport)

    def calls(self):
        with open(os.path.join(self.out_dir, "judge-calls.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def fact_calls(self, component_id=None):
        return [c for c in self.calls() if c["task"] == "fact" and component_id in (None, c["component_id"])]

    def forget_calls(self):
        path = os.path.join(self.out_dir, "judge-calls.jsonl")
        if os.path.exists(path):
            os.remove(path)

    def edited_ground_truth(self, mutate) -> None:
        gt = copy.deepcopy(GROUND_TRUTH)
        mutate(gt["articles"][0])
        self.use_ground_truth(gt)


def ground_truth_for(*articles):
    """每個 (文章 id, domain) 一篇，結構與 GROUND_TRUTH 的 9000001 相同。"""
    out = []
    for article_id, domain in articles:
        gt = copy.deepcopy(GROUND_TRUTH["articles"][0])
        gt["article_id"], gt["domain"] = article_id, domain
        for item in gt["items"]:
            item["component_id"] = item["component_id"].replace("9000001", article_id)
        out.append(gt)
    return {"articles": out}


def translation_without(component_id: str):
    """ok_response 的譯文拿掉一個 Component 之後的回應。"""
    translation = json.loads(json.loads(ok_response().body)["candidates"][0]["content"]["parts"][0]["text"])
    translation["components"] = [c for c in translation["components"] if c["id"] != component_id]
    return gemini_samples.ok(json.dumps(translation, ensure_ascii=False))


def combined_transport(mqm=None, fact=None, screen=None):
    """三種請求的假 Judge：fact 的請求（有 sentences）交給 fact_transport，全文篩查的（有 components）交給 screen_transport，其餘交給 judge_transport。"""
    mqm_side, fact_side, screen_side = judge_transport(mqm), fact_transport(fact), screen_transport(screen)

    def reply(request):
        payload = user_payload(request)
        side = fact_side if "sentences" in payload else screen_side if "components" in payload else mqm_side
        return side.send(request)

    return FunctionTransport(reply)


S1_FACT, S2_FACT = "工程延誤半年", "不滿的是市民陳虛構本人"
WEAK = {"verdict": "Weakened", "type": "e", "quote": "late", "reason": "虛構的理由：語氣被削弱"}
KEY = re.compile(r"`([a-z-]+-[0-9a-f]{16})`")
HASH = re.compile(r"輸入 hash：`([0-9a-f]{16})`")


class ReviewCase(FactCase):
    def run_judges(self, mqm=None, fact=None, screen=None):
        code, _, err = self.run_cli("judge", transport=combined_transport(mqm, fact, screen))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("check")[0], 0)

    def review(self, expect=0):
        code, out, err = self.run_cli("review")
        self.assertEqual(code, expect, err)
        return out, err

    def review_text(self) -> str:
        self.review()
        with open(os.path.join(self.out_dir, "review.md"), encoding="utf-8") as f:
            return f.read()

    def report_text(self) -> str:
        code, _, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        return self.output_text("report.md")

    @staticmethod
    def part(text: str, heading: str) -> str:
        """## 必看 或 ## 抽查 底下的內容。"""
        return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]

    @staticmethod
    def item(text: str, needle: str) -> dict:
        """審閱清單裡含 needle 的那個項目：{key, hash, text}。"""
        chunks = [c for c in text.split("\n#### ")[1:] if needle in c]
        assert len(chunks) == 1, f"含 {needle!r} 的項目有 {len(chunks)} 個"
        return {"key": KEY.search(chunks[0]).group(1), "hash": HASH.search(chunks[0]).group(1), "text": chunks[0]}

    def decide(self, item: dict, action: str, **fields) -> None:
        path = os.path.join(self.out_dir, "review-decisions.json")
        decisions = json.loads(Path(path).read_text(encoding="utf-8"))["decisions"] if os.path.exists(path) else []
        decisions.append({"key": item["key"], "input_hash": item["hash"], "action": action, "reviewer": "虛構審閱者",
                          "reviewed_on": "2026-11-09", **fields})
        self.write_json(path, {"decisions": decisions})

    def set_review(self, **review) -> None:
        self.extra_experiment["review"] = review
        self.write_json(self.config_path, self.experiment())

    def all_kinds(self):
        """必看每一種都出現的世界：Gate 失敗、uncertain 的事實與 Finding、無效的輸出、MQM 的 Unjudged，另有一個一致的 Finding。"""
        self.run_judges(
            mqm={("9000001-001", 1): {"findings": [finding()], "source_defects": []},
                 "9000001-002": gemini_samples.prompt_blocked(),
                 ("9000001-003", 1): {"findings": [finding(source_span="不存在的片段")], "source_defects": []},
                 "title": {"findings": [finding(dimension="Fluency", subtype="awkward", severity="minor",
                                                source_span="翻新工程延誤", target_span="renovation delayed")], "source_defects": []}},
            fact={"9000001-001": lambda p: fact_output(p, {S1_FACT: WEAK}),
                  ("9000001-002", 2): lambda p: fact_output(p, {S2_FACT: WEAK})})
