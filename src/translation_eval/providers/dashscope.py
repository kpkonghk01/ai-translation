"""阿里雲 DashScope（Model Studio）的 OpenAI 相容 chat completions，只用 International（新加坡）endpoint。

解讀規則照官方文件的 response body 與錯誤碼表（2026-11-09 核對）。內容審查的錯誤是 DataInspectionFailed
（OpenAI 相容模式寫作 data_inspection_failed）：HTTP 400，沒有任何譯文，記為 block。文件沒有寫錯誤 body 的格式，
所以 error 物件和頂層 code 兩種寫法都認。不認識的回應格式一律是 error，絕不當作 ok。
"""
import json
import re

from ..outcome import Interpretation, UnrecognisedResponse, classify_text, token_count
from ..schemas import ApproachConfig, DASHSCOPE_DEFAULT_BASE_URL, Outcome
from ..transport import Request, Response

# 平台因為內容而拒絕處理的錯誤碼（官方錯誤碼文件），比對時不分大小寫、不計底線與點
BLOCK_CODES = {"datainspectionfailed", "faqruleblocked", "customroleblocked", "ipinfringementsuspect"}
_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.]{2,60}")


def _normalised(code: str) -> str:
    return re.sub(r"[_.]", "", code).lower()


def _error_codes(body) -> list[str]:
    """錯誤 body 裡的錯誤碼：error.code、error.type 或頂層的 code。只收長得像錯誤碼的字串，不抄 message。"""
    if not isinstance(body, dict):
        return []
    error = body.get("error")
    candidates = [error.get("code"), error.get("type")] if isinstance(error, dict) else []
    candidates.append(body.get("code"))
    return [c for c in candidates if isinstance(c, str) and _CODE.fullmatch(c)]


class DashscopeAdapter:
    def endpoint(self, approach: ApproachConfig) -> str:
        return (approach.base_url or DASHSCOPE_DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"

    def build_request(self, approach: ApproachConfig, system_prompt: str, user_text: str, api_key: str) -> Request:
        body = {
            "model": approach.model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
            "response_format": {"type": "json_object"},
        }
        if approach.temperature is not None:
            body["temperature"] = approach.temperature
        if approach.max_output_tokens is not None:
            body["max_completion_tokens"] = approach.max_output_tokens
        headers = {"content-type": "application/json", "Authorization": f"Bearer {api_key}"}
        if approach.data_inspection is not None:   # 只有 Approach 設定明確寫了才送；值照設定原樣送出
            headers["X-DashScope-DataInspection"] = approach.data_inspection
        return Request("POST", self.endpoint(approach), headers, json.dumps(body, ensure_ascii=False).encode("utf-8"), approach.timeout_seconds)

    def interpret(self, response: Response) -> Interpretation:
        try:
            body = json.loads(response.body)
        except ValueError:
            body = None
        if response.status != 200:
            return self._http_error(response.status, body)
        try:
            return self._interpret_completion(body)
        except UnrecognisedResponse:
            return Interpretation(Outcome.ERROR, "回應格式不認識")

    @staticmethod
    def _interpret_completion(body) -> Interpretation:
        if not isinstance(body, dict):
            raise UnrecognisedResponse
        usage = body.get("usage") if body.get("usage") is not None else {}
        details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
        model_version = body.get("model")
        if not isinstance(usage, dict) or not (model_version is None or isinstance(model_version, str)):
            raise UnrecognisedResponse
        meta = {
            "input_tokens": token_count(usage.get("prompt_tokens")),
            "output_tokens": token_count(usage.get("completion_tokens")),
            "thought_tokens": token_count(details.get("reasoning_tokens")) if isinstance(details, dict) else None,
            "model_version": model_version,
        }
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise UnrecognisedResponse
        message, finish = choices[0].get("message"), choices[0].get("finish_reason")
        if not isinstance(message, dict):
            raise UnrecognisedResponse
        if finish == "content_filter":   # OpenAI 相容的標準值；官方文件沒有列，但意思就是被過濾，不能當成正常結束
            return Interpretation(Outcome.BLOCK, "output:content_filter", **meta)
        if finish == "length":
            return Interpretation(Outcome.TRUNCATED, "length", **meta)
        if finish != "stop":
            return Interpretation(Outcome.ERROR, f"finish_reason {str(finish)[:40]}", **meta)
        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal.strip():   # OpenAI 相容的 refusal 欄位：模型自己拒絕
            return Interpretation(Outcome.REFUSAL, "message.refusal 有內容", **meta)
        content = message.get("content")
        if not isinstance(content, str):
            raise UnrecognisedResponse
        outcome, reason, payload = classify_text(content)
        return Interpretation(outcome, reason, payload, **meta)

    @staticmethod
    def _http_error(status: int, body) -> Interpretation:
        codes = _error_codes(body)
        blocked = next((c for c in codes if _normalised(c) in BLOCK_CODES), None)
        if blocked:   # 內容審查：即使狀態碼是 429 或 5xx 也不重試，重複嘗試要靠增加 Run 次數
            return Interpretation(Outcome.BLOCK, blocked)
        if status == 429 or status >= 500:
            return Interpretation(Outcome.ERROR, f"HTTP {status}", retryable=True)
        return Interpretation(Outcome.ERROR, f"HTTP {status}" + (f" {codes[0]}" if codes else ""))
