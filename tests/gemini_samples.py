"""Gemini generateContent 的回應樣本，格式照官方 API reference 的 GenerateContentResponse。

所有測試都從這裡取樣本，不各自手寫 JSON。正文只用虛構文字。
"""
import json

from translation_eval.transport import Response

MODEL_VERSION = "gemini-3.1-flash-lite-fake"


def _response(status: int, body) -> Response:
    raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
    return Response(status=status, headers={"content-type": "application/json"}, body=raw)


def _usage(prompt_tokens: int, output_tokens: int, thought_tokens: int) -> dict:
    return {"promptTokenCount": prompt_tokens, "candidatesTokenCount": output_tokens,
            "thoughtsTokenCount": thought_tokens, "totalTokenCount": prompt_tokens + output_tokens + thought_tokens}


def _candidate(text: str, finish_reason: str) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish_reason, "index": 0}],
            "usageMetadata": _usage(120, 80, 30), "modelVersion": MODEL_VERSION}


def ok(text: str) -> Response:
    """正常譯文：finishReason STOP，內容是預期的 JSON 文字。"""
    return _response(200, _candidate(text, "STOP"))


def prompt_blocked(reason: str = "PROHIBITED_CONTENT") -> Response:
    """整個請求在輸入端被擋：只有 promptFeedback.blockReason，沒有 candidates。"""
    return _response(200, {"promptFeedback": {"blockReason": reason}, "usageMetadata": {"promptTokenCount": 120, "totalTokenCount": 120},
                           "modelVersion": MODEL_VERSION})


def safety_stopped(partial_text: str, finish_reason: str = "SAFETY") -> Response:
    """輸出到一半被安全機制截斷：finishReason 是安全類，但已有部分輸出。"""
    return _response(200, _candidate(partial_text, finish_reason))


def max_tokens(partial_text: str) -> Response:
    """超出 maxOutputTokens 而被截斷，與安全機制無關。"""
    return _response(200, _candidate(partial_text, "MAX_TOKENS"))


def refusal(text: str = "I'm sorry, but I can't help with translating this article.") -> Response:
    """模型正常回應（STOP），但內容是拒絕語，不是 JSON。"""
    return _response(200, _candidate(text, "STOP"))


def prose_not_refusal(text: str = "Here is the English version of the article, with every component translated.") -> Response:
    """不是 JSON，但也不是拒絕語。"""
    return _response(200, _candidate(text, "STOP"))


def rate_limited() -> Response:
    return _response(429, {"error": {"code": 429, "message": "Resource has been exhausted.", "status": "RESOURCE_EXHAUSTED"}})


def server_error(status: int = 503) -> Response:
    return _response(status, {"error": {"code": status, "message": "The service is unavailable.", "status": "UNAVAILABLE"}})


def candidate_with(content, finish_reason: str = "STOP") -> Response:
    """candidates[0].content 由呼叫者指定：用來造出結構怪異的回應。"""
    return _response(200, {"candidates": [{"content": content, "finishReason": finish_reason}], "modelVersion": MODEL_VERSION})


def with_fields(response: Response, **fields) -> Response:
    """在既有回應的 JSON 頂層加欄位或覆蓋欄位。"""
    body = json.loads(response.body)
    body.update(fields)
    return _response(response.status, body)


def not_json(status: int = 200) -> Response:
    """例如中途的代理伺服器回了一頁 HTML。"""
    return Response(status=status, headers={"content-type": "text/html"}, body=b"<html>proxy page</html>")


def client_error(status: int = 400) -> Response:
    """不會因重試而好轉的 4xx（例如請求本身不合法）。"""
    return _response(status, {"error": {"code": status, "message": "Invalid value.", "status": "INVALID_ARGUMENT"}})


def unknown_format() -> Response:
    """HTTP 200，但 JSON 結構不是 GenerateContentResponse。"""
    return _response(200, {"result": "something else entirely"})
