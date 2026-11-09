"""Gemini（AI Studio，付費 key）的 generateContent。

解讀規則照官方 API reference 的 GenerateContentResponse（2026-11-06 核對）。
不認識的格式一律是 error，絕不當作 ok；任何欄位的型別不符也是不認識的格式，而不是讓程式拋出例外：
一個怪異的回應不可以令整批執行中斷。
"""
import json
import re

from ..outcome import Interpretation, UnrecognisedResponse, classify_text, lenient, token_count
from ..schemas import ApproachConfig, Outcome
from ..transport import Request, Response

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# safety settings 全部調到最低：官方文件列出的 category，threshold 用最寬鬆的 OFF。
# 只剩 Google 不可關的保護（例如 PROHIBITED_CONTENT）。
SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_JAILBREAK",
)
LOWEST_THRESHOLD = "OFF"

# 安全類的 finishReason 一律是 Block。RECITATION、LANGUAGE、OTHER 等不是安全機制，不認識的值記為 error。
BLOCK_FINISH_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}

UNRECOGNISED = Interpretation(Outcome.ERROR, "回應格式不認識")


def _output_text(candidate: dict) -> str:
    content = candidate.get("content")
    if not isinstance(content, dict):
        raise UnrecognisedResponse
    if "parts" not in content:   # 正常結束但沒有任何輸出
        return ""
    parts = content["parts"]
    if not isinstance(parts, list) or not all(isinstance(p, dict) and isinstance(p.get("text", ""), str) for p in parts):
        raise UnrecognisedResponse
    return "".join(p.get("text", "") for p in parts)


class GeminiAdapter:
    def endpoint(self, approach: ApproachConfig) -> str:
        return ENDPOINT.format(model=approach.model)

    def build_request(self, approach: ApproachConfig, system_prompt: str, user_text: str, api_key: str) -> Request:
        generation = {"responseMimeType": "application/json"} if approach.request_mode == "article-json" else {}
        if approach.temperature is not None:
            generation["temperature"] = approach.temperature
        if approach.max_output_tokens is not None:
            generation["maxOutputTokens"] = approach.max_output_tokens
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": generation,
            "safetySettings": [{"category": c, "threshold": LOWEST_THRESHOLD} for c in SAFETY_CATEGORIES],
        }
        # key 放在 header：網址會出現在 dry-run 與錯誤訊息裡，key 不可以
        headers = {"content-type": "application/json", "x-goog-api-key": api_key}
        return Request("POST", self.endpoint(approach), headers, json.dumps(body, ensure_ascii=False).encode("utf-8"), approach.timeout_seconds)

    def problems(self, approach: ApproachConfig) -> list[str]:
        return []

    def describe(self, approach: ApproachConfig) -> list[str]:
        return []

    def interpret(self, response: Response, approach: ApproachConfig, classify=classify_text) -> Interpretation:
        try:
            body = json.loads(response.body)
        except (ValueError, RecursionError):
            body = None
        if response.status != 200:
            return self._http_error(response.status, body)
        if not isinstance(body, dict):
            return UNRECOGNISED
        try:
            return self._interpret_ok_status(body, classify)
        except UnrecognisedResponse:
            return UNRECOGNISED

    @staticmethod
    def _meta(body: dict) -> dict:
        usage = body.get("usageMetadata")
        if usage is None:
            usage = {}
        model_version = body.get("modelVersion")
        if not isinstance(usage, dict) or not (model_version is None or isinstance(model_version, str)):
            raise UnrecognisedResponse
        return {
            "input_tokens": token_count(usage.get("promptTokenCount")),
            "output_tokens": token_count(usage.get("candidatesTokenCount")),
            "thought_tokens": token_count(usage.get("thoughtsTokenCount")),
            "model_version": model_version,
        }

    def _interpret_ok_status(self, body: dict, classify) -> Interpretation:
        # 只有 ok 的 Run 才要求 token 數和模型版本正確；其他結果類別不能因為這些附帶資料怪怪的而變成 error
        meta = lenient(self._meta, body)
        feedback = body.get("promptFeedback")
        block_reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
        if isinstance(block_reason, str) and block_reason != "BLOCK_REASON_UNSPECIFIED":
            return Interpretation(Outcome.BLOCK, f"prompt:{block_reason}", **meta)   # 輸入端被擋，沒有 candidates
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise UnrecognisedResponse
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if finish in BLOCK_FINISH_REASONS:   # 即使已有部分輸出也是 Block：殘缺的譯文不能拿去評分
            return Interpretation(Outcome.BLOCK, f"output:{finish}", **meta)
        if finish == "MAX_TOKENS":
            return Interpretation(Outcome.TRUNCATED, "MAX_TOKENS", **meta)
        if finish != "STOP":
            return Interpretation(Outcome.ERROR, f"finishReason {str(finish)[:40]}", **meta)
        outcome, reason, payload = classify(_output_text(candidate))
        if outcome is Outcome.OK:
            meta = self._meta(body)
        return Interpretation(outcome, reason, payload, **meta)

    @staticmethod
    def _http_error(status: int, body) -> Interpretation:
        if status == 429 or status >= 500:   # 暫時性：交給共用的重試層
            return Interpretation(Outcome.ERROR, f"HTTP {status}", retryable=True)
        error = body.get("error") if isinstance(body, dict) else None
        google_status = error.get("status") if isinstance(error, dict) else None
        # 只記 Google 的狀態代碼（例如 INVALID_ARGUMENT），不抄 message
        suffix = f" {google_status}" if isinstance(google_status, str) and re.fullmatch(r"[A-Z_]{3,40}", google_status) else ""
        return Interpretation(Outcome.ERROR, f"HTTP {status}{suffix}")
