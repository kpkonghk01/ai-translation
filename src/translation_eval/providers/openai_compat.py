"""OpenAI 相容的 chat completions 回應的共用解讀（阿里雲 DashScope、OpenRouter 都用）。

只處理 200 的 completion：choices、finish_reason、usage、message.refusal、content。HTTP 錯誤和各家自己的
錯誤碼（內容審查等）由各 adapter 處理。不認識的格式一律是 error，絕不當作 ok。
"""
from ..outcome import Interpretation, UnrecognisedResponse, classify_text, lenient, token_count
from ..schemas import Outcome


def usage_meta(body: dict) -> dict:
    usage = body.get("usage") if body.get("usage") is not None else {}
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    model_version = body.get("model")
    if not isinstance(usage, dict) or not (model_version is None or isinstance(model_version, str)):
        raise UnrecognisedResponse
    return {
        "input_tokens": token_count(usage.get("prompt_tokens")),
        "output_tokens": token_count(usage.get("completion_tokens")),
        "thought_tokens": token_count(details.get("reasoning_tokens")) if isinstance(details, dict) else None,
        "model_version": model_version,
    }


def interpret_completion(body, classify=classify_text) -> Interpretation:
    if not isinstance(body, dict):
        raise UnrecognisedResponse
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise UnrecognisedResponse
    choice = choices[0]
    finish = choice.get("finish_reason")
    # 只有 ok 的 Run 才要求 token 數和模型版本正確；其他結果類別不能因為這些附帶資料怪怪的而變成 error
    meta = lenient(usage_meta, body)
    if finish == "content_filter":   # 被過濾，不能當成正常結束
        return Interpretation(Outcome.BLOCK, "output:content_filter", **meta)
    if finish == "error" or choice.get("error") is not None:   # 錯誤也可能在 choice 裡，旁邊還有部分輸出
        return Interpretation(Outcome.ERROR, "provider 中途出錯", **meta)
    if finish == "length":
        return Interpretation(Outcome.TRUNCATED, "length", **meta)
    if finish != "stop":
        return Interpretation(Outcome.ERROR, f"finish_reason {str(finish)[:40]}", **meta)
    message = choice.get("message")
    if not isinstance(message, dict):
        raise UnrecognisedResponse
    refusal = message.get("refusal")
    if refusal is not None and not isinstance(refusal, str):
        raise UnrecognisedResponse
    if refusal and refusal.strip():   # message.refusal：模型自己拒絕
        return Interpretation(Outcome.REFUSAL, "message.refusal 有內容", **meta)
    content = message.get("content")
    if not isinstance(content, str):
        raise UnrecognisedResponse
    outcome, reason, payload = classify(content)
    if outcome is Outcome.OK:
        meta = usage_meta(body)
    return Interpretation(outcome, reason, payload, **meta)
