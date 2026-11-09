"""阿里雲 DashScope（Model Studio）的 OpenAI 相容 chat completions，只用 International（新加坡）endpoint。

解讀規則照官方文件的 response body 與錯誤碼表（2026-11-09 核對）。內容審查的錯誤是 DataInspectionFailed
（OpenAI 相容模式寫作 data_inspection_failed）：沒有任何譯文，記為 block。文件寫的是 HTTP 400，但不論狀態碼是什麼，
body 裡有這些錯誤碼就是 block：Block 率不能因為狀態碼不如預期而少算。文件沒有寫錯誤 body 的格式，所以 error 物件和
頂層 code 兩種寫法都認。不認識的回應格式一律是 error，絕不當作 ok。
"""
import json
import re

from ..outcome import Interpretation, UnrecognisedResponse, classify_text
from ..schemas import ApproachConfig, DASHSCOPE_DEFAULT_BASE_URL, Outcome
from ..transport import Request, Response
from .openai_compat import interpret_completion

# 平台因為內容而拒絕處理的錯誤碼（官方錯誤碼文件）：比對時不分大小寫、不計底線與點，報告裡一律寫成官方的寫法
BLOCK_CODES = {"datainspectionfailed": "DataInspectionFailed", "faqruleblocked": "FaqRuleBlocked",
               "customroleblocked": "CustomRoleBlocked", "ipinfringementsuspect": "IPInfringementSuspect"}
_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.]{2,60}")


def _error_codes(body) -> list[str]:
    """錯誤 body 裡的錯誤碼：error.code、error.type 或頂層的 code。只收長得像錯誤碼的字串，不抄 message。"""
    if not isinstance(body, dict):
        return []
    error = body.get("error")
    candidates = [error.get("code"), error.get("type")] if isinstance(error, dict) else []
    candidates.append(body.get("code"))
    return [c for c in candidates if isinstance(c, str) and _CODE.fullmatch(c)]


def _block_code(body) -> str | None:
    return next((BLOCK_CODES[key] for c in _error_codes(body) if (key := re.sub(r"[_.]", "", c).lower()) in BLOCK_CODES), None)


class DashscopeAdapter:
    def endpoint(self, approach: ApproachConfig) -> str:
        return (approach.base_url or DASHSCOPE_DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"

    def build_request(self, approach: ApproachConfig, system_prompt: str, user_text: str, api_key: str) -> Request:
        body = {
            "model": approach.model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
        }
        if approach.request_mode == "article-json":
            body["response_format"] = {"type": "json_object"}
        if approach.temperature is not None:
            body["temperature"] = approach.temperature
        if approach.max_output_tokens is not None:
            body["max_tokens"] = approach.max_output_tokens   # 每個 Qwen 模型都支援；max_completion_tokens 只有較新的模型有文件
        headers = {"content-type": "application/json", "Authorization": f"Bearer {api_key}"}
        if approach.data_inspection is not None:   # 只有 Approach 設定明確寫了才送；值照設定原樣送出
            headers["X-DashScope-DataInspection"] = approach.data_inspection
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
        blocked = _block_code(body)
        if blocked:   # 內容審查：不重試，重複嘗試要靠增加 Run 次數
            return Interpretation(Outcome.BLOCK, blocked)
        if response.status != 200:
            return self._http_error(response.status, body)
        try:
            return interpret_completion(body, classify)
        except UnrecognisedResponse:
            return Interpretation(Outcome.ERROR, "回應格式不認識")

    @staticmethod
    def _http_error(status: int, body) -> Interpretation:
        codes = _error_codes(body)
        if status == 429 or status >= 500:
            return Interpretation(Outcome.ERROR, f"HTTP {status}", retryable=True)
        return Interpretation(Outcome.ERROR, f"HTTP {status}" + (f" {codes[0]}" if codes else ""))
