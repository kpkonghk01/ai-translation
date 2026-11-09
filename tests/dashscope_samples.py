"""阿里雲 DashScope（OpenAI 相容 chat completions）的回應樣本，格式照官方文件的 response body 與錯誤碼表。

錯誤碼取自官方錯誤碼文件：內容審查是 DataInspectionFailed（OpenAI 相容模式寫作 data_inspection_failed）。
文件沒有寫錯誤 body 的格式，所以樣本有兩種寫法：error 物件，以及頂層的 code。
"""
import json

from translation_eval.transport import Response

MODEL_VERSION = "qwen-plus-fake"


def _response(status: int, body) -> Response:
    raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
    return Response(status=status, headers={"content-type": "application/json"}, body=raw)


def _completion(message: dict, finish_reason: str | None = "stop") -> dict:
    return {"id": "chatcmpl-fake", "object": "chat.completion", "created": 1, "model": MODEL_VERSION,
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200,
                      "completion_tokens_details": {"reasoning_tokens": 30}}}


def ok(text: str) -> Response:
    return _response(200, _completion({"content": text}))


def with_fields(response: Response, **fields) -> Response:
    body = json.loads(response.body)
    body.update(fields)
    return _response(response.status, body)


def finished(text: str, finish_reason: str) -> Response:
    """finish_reason 不是 stop：length 是超出長度，content_filter 是被過濾。"""
    return _response(200, _completion({"content": text}, finish_reason))


def provider_refusal(text: str = "I cannot help with that.") -> Response:
    """OpenAI 相容的 message.refusal 欄位有內容。"""
    return _response(200, _completion({"content": None, "refusal": text}))


def malformed_message(message) -> Response:
    body = _completion({})
    body["choices"][0]["message"] = message
    return _response(200, body)


def error(status: int, code: str, style: str = "object") -> Response:
    message = "fake message"
    if style == "object":
        body = {"error": {"code": code, "message": message, "type": code}, "request_id": "req-fake"}
    else:
        body = {"code": code, "message": message, "request_id": "req-fake"}
    return _response(status, body)


def data_inspection_failed(style: str = "object") -> Response:
    return error(400, "data_inspection_failed" if style == "object" else "DataInspectionFailed", style)


def rate_limited() -> Response:
    return error(429, "Throttling.RateQuota")


def server_error(status: int = 503) -> Response:
    return error(status, "ServiceUnavailable")


def invalid_key() -> Response:
    return error(401, "invalid_api_key")


def unknown_format() -> Response:
    return _response(200, {"result": "something else entirely"})


def not_json(status: int = 200) -> Response:
    return Response(status=status, headers={"content-type": "text/html"}, body=b"<html>gateway</html>")
