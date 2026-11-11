"""OpenRouter（OpenAI 相容 chat completions）的回應樣本，格式照官方文件。

文件寫明：非串流的回應也可能是 200 但 body 只有 error 物件；403 包括 guardrail 與 moderation（moderation 的
metadata 有 reasons）；finish_reason 有 content_filter 與 error。回應裡寫的 provider 是顯示名稱（例如 DeepInfra），
和請求的 only 用的 slug（deepinfra）寫法不同。
"""
import json

from translation_eval.transport import Response

MODEL_VERSION = "tencent/hy-mt2-fake"


def _response(status: int, body) -> Response:
    raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
    return Response(status=status, headers={"content-type": "application/json"}, body=raw)


def _completion(message: dict, finish_reason, provider) -> dict:
    body = {"id": "gen-fake", "object": "chat.completion", "created": 1, "model": MODEL_VERSION,
            "choices": [{"index": 0, "finish_reason": finish_reason, "native_finish_reason": "stop",
                         "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200,
                      "completion_tokens_details": {"reasoning_tokens": 30}}}
    if provider is not None:
        body["provider"] = provider
    return body


def ok(text: str, provider: str | None = "DeepInfra") -> Response:
    return _response(200, _completion({"content": text}, "stop", provider))


def with_content(content, provider: str | None = "DeepInfra") -> Response:
    """正常結束（stop），但 content 不是文字：例如 null（Tencent 的 Hy-MT2 對部分輸入會這樣）。"""
    return _response(200, _completion({"content": content}, "stop", provider))


def finished(text: str, finish_reason, provider: str | None = "DeepInfra") -> Response:
    return _response(200, _completion({"content": text}, finish_reason, provider))


def with_fields(response: Response, **fields) -> Response:
    body = json.loads(response.body)
    body.update(fields)
    return _response(response.status, body)


def provider_refusal(text: str = "I cannot help with that.") -> Response:
    return _response(200, _completion({"content": None, "refusal": text}, "stop", "DeepInfra"))


def error_body(code: int, message: str = "fake message", metadata: dict | None = None) -> dict:
    error = {"code": code, "message": message}
    if metadata is not None:
        error["metadata"] = metadata
    return {"error": error}


def error(status: int, code: int | None = None, metadata: dict | None = None) -> Response:
    return _response(status, error_body(code if code is not None else status, metadata=metadata))


def error_with_200(code: int, metadata: dict | None = None) -> Response:
    """文件：非串流的回應也可能是 200，但 body 只有 error 物件、沒有 choices。"""
    return _response(200, error_body(code, metadata=metadata))


def moderation(status: int = 403, reasons=("sexual",)) -> Response:
    return error(status, 403, {"reasons": list(reasons), "flagged_input": "fake", "provider_name": "OpenAI", "model_slug": "fake"})


def choice_error(partial: str = '{"title": "Fict') -> Response:
    """文件：錯誤也可能在 choice 裡，旁邊還有部分輸出，finish_reason 是 error。"""
    body = _completion({"content": partial}, "error", "DeepInfra")
    body["choices"][0]["error"] = {"code": 502, "message": "fake"}
    return _response(200, body)


def deeply_nested(status: int = 200) -> Response:
    return Response(status=status, headers={"content-type": "application/json"}, body=b"[" * 200000)


def not_json(status: int = 200) -> Response:
    return Response(status=status, headers={"content-type": "text/html"}, body=b"<html>gateway</html>")


def unknown_format() -> Response:
    return _response(200, {"result": "something else entirely"})


def endpoints(*providers: dict, **extra) -> Response:
    """GET /api/v1/models/{author}/{slug}/endpoints 的回應。"""
    return _response(200, {"data": {"id": "tencent/hy-mt2", "name": "Tencent: HY-MT2", "endpoints": [dict(p) for p in providers]}, **extra})


def endpoint(provider_name: str, tag: str, **fields) -> dict:
    return {"provider_name": provider_name, "tag": tag, "name": f"{provider_name}: HY-MT2", "quantization": "fp8",
            "context_length": 32768, "status": 0, "uptime_last_30m": 99.5, **fields}
