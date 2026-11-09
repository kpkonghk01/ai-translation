"""Gate 測試共用：Must-retain fact 的測試用 Workspace、虛構的 ground truth 和譯文的改動。只用虛構資料。"""
import copy
import json
import os

import gemini_samples
from fake_transport import FakeTransport
from fixtures import GROUND_TRUTH, Workspace, ok_response


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
