"""Judge 測試共用：假的 Judge 回應。回應由函式決定，因為每個 Component 要評兩次，請求數事先不固定。"""
import json
from collections import Counter

import gemini_samples
from fake_transport import FunctionTransport

NO_PROBLEMS = {"findings": [], "source_defects": []}


def finding(dimension="Accuracy", severity="major", source_span="預算超支", target_span="over budget", subtype="mistranslation", reason="虛構的理由"):
    return {"dimension": dimension, "subtype": subtype, "severity": severity, "source_span": source_span, "target_span": target_span, "reason": reason}


def user_payload(request) -> dict:
    return json.loads(json.loads(request.body)["contents"][0]["parts"][0]["text"])


def system_text(request) -> str:
    return json.loads(request.body)["systemInstruction"]["parts"][0]["text"]


def judge_transport(outputs=None):
    """outputs：{Component id: 輸出} 或 {(Component id, 第幾次): 輸出}；輸出可以是 dict（包成正常回應）、Response 或例外。
    沒有指定的 Component 回沒有任何問題。"""
    outputs, seen = outputs or {}, Counter()

    def reply(request):
        component_id = user_payload(request)["component"]["id"]
        seen[component_id] += 1
        item = outputs.get((component_id, seen[component_id]), outputs.get(component_id, NO_PROBLEMS))
        return gemini_samples.ok(json.dumps(item, ensure_ascii=False)) if isinstance(item, dict) else item

    return FunctionTransport(reply)
