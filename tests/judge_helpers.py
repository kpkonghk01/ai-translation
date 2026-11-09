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


# ───────────── Must-retain fact 任務 ─────────────

def verdict_entry(payload: dict, fact: dict, **changes) -> dict:
    """一條事實的 verdict：預設是 Present，引語取譯文的開頭（一定是逐字子字串）。"""
    entry = {"fact_id": fact["id"], "verdict": "Present", "type": None, "quote": payload["component"]["translation"][:20], "reason": "虛構的理由"}
    return entry | changes


def fact_output(payload: dict, changes: dict | None = None) -> dict:
    """這個請求的所有事實都判 Present，除了 changes 指定的：{事實文字: 要覆寫的欄位}。"""
    changes = changes or {}
    return {"verdicts": [verdict_entry(payload, fact, **changes.get(fact["fact"], {})) for s in payload["sentences"] for fact in s["facts"]]}


def fact_transport(outputs=None):
    """outputs：{Component id: 輸出} 或 {(Component id, 第幾次): 輸出}；輸出可以是 dict、函式（收到請求的內容，回傳 dict）、Response 或例外。
    沒有指定的 Component 全部判 Present。MQM 的請求回沒有任何問題。"""
    outputs, seen = outputs or {}, Counter()

    def reply(request):
        payload = user_payload(request)
        if "sentences" not in payload:
            return gemini_samples.ok(json.dumps(NO_PROBLEMS))
        component_id = payload["component"]["id"]
        seen[component_id] += 1
        item = outputs.get((component_id, seen[component_id]), outputs.get(component_id, fact_output))
        item = item(payload) if callable(item) else item
        return gemini_samples.ok(json.dumps(item, ensure_ascii=False)) if isinstance(item, dict) else item

    return FunctionTransport(reply)
