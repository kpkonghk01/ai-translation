"""OpenRouter 的 OpenAI 相容 chat completions。每個請求必定帶資料保護欄位，這些規則套用於 OpenRouter 上的所有請求。

請求的 provider：zdr: true、data_collection: "deny"、only（取自 Pan 已確認的允許清單）、allow_fallbacks: false。
允許清單是空的或還沒確認時，拒絕組出請求。回應寫的 provider 不在 only 內（或根本沒寫）時記為 error：
資料有沒有送到未確認的地方，要能從紀錄看出來。content_filter 與 moderation 錯誤記為 block。
解讀規則照官方文件（2026-11-09 核對）：非串流的回應也可能是 200，但 body 只有 error 物件；403 包括 guardrail 與
moderation，moderation 的 metadata 有 reasons；錯誤也可能在 choice 裡。不認識的回應格式一律是 error，絕不當作 ok。
"""
import json
import re
from dataclasses import replace

from ..errors import InputError
from ..outcome import Interpretation, UnrecognisedResponse, classify_text
from ..schemas import ApproachConfig, Outcome
from ..transport import Request, Response
from .openai_compat import interpret_completion

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_REASON = re.compile(r"[A-Za-z0-9_/-]{1,30}")
MAX_REASONS = 5


def _normalised(name: str) -> str:
    """回應寫的是顯示名稱（Google Vertex），請求的 only 用的是 slug（google-vertex）：只比較字母和數字。"""
    return re.sub(r"[^a-z0-9]", "", name.lower())


class OpenRouterAdapter:
    def endpoint(self, approach: ApproachConfig) -> str:
        return ENDPOINT

    def problems(self, approach: ApproachConfig) -> list[str]:
        routing = approach.routing
        if routing is None or not routing.only or routing.confirmed_on is None:
            return [f"Approach {approach.id}：OpenRouter 的 provider 允許清單是空的或還沒確認，不送出任何請求"
                    "（先用 providers 指令看清單，確認後把 routing.only 和 routing.confirmed_on 寫進 Approach 設定）"]
        return []

    def describe(self, approach: ApproachConfig) -> list[str]:
        problems = self.problems(approach)
        if problems:
            return [f"routing：{approach.id} 允許清單尚未確認（真跑會被拒絕）"]
        routing = approach.routing
        return [f"routing：{approach.id} zdr、data_collection=deny、allow_fallbacks=false、only={','.join(routing.only)}"
                f"（確認日期 {routing.confirmed_on}）"]

    def build_request(self, approach: ApproachConfig, system_prompt: str, user_text: str, api_key: str) -> Request:
        problems = self.problems(approach)
        if problems:   # 呼叫者應該已經用 problems() 擋下；這裡再擋一次，沒有保護欄位的請求不可以組出來
            raise InputError(problems)
        body = {
            "model": approach.model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
            "provider": {"zdr": True, "data_collection": "deny", "only": list(approach.routing.only), "allow_fallbacks": False},
        }
        if approach.request_mode == "article-json":
            body["response_format"] = {"type": "json_object"}
        if approach.temperature is not None:
            body["temperature"] = approach.temperature
        if approach.max_output_tokens is not None:
            body["max_tokens"] = approach.max_output_tokens
        headers = {"content-type": "application/json", "Authorization": f"Bearer {api_key}"}
        return Request("POST", ENDPOINT, headers, json.dumps(body, ensure_ascii=False).encode("utf-8"), approach.timeout_seconds)

    def interpret(self, response: Response, approach: ApproachConfig, classify=classify_text) -> Interpretation:
        try:
            body = json.loads(response.body)
        except (ValueError, RecursionError):
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        if response.status != 200 or (isinstance(error, dict) and not body.get("choices")):
            return self._error(response.status, error)   # 200 也可能只有 error 物件、沒有 choices
        try:
            return self._check_served_provider(interpret_completion(body, classify), body, approach)
        except UnrecognisedResponse:
            return Interpretation(Outcome.ERROR, "回應格式不認識")

    @staticmethod
    def _check_served_provider(interpretation: Interpretation, body: dict, approach: ApproachConfig) -> Interpretation:
        """資料已經送出去了，不論結果是什麼，都要確認是哪個 provider 收到的。"""
        served = body.get("provider")
        if not isinstance(served, str) or not served.strip():
            return replace(interpretation, outcome=Outcome.ERROR, reason="回應沒有寫是哪個 provider 服務的", payload=None)
        served = served.strip()[:60]
        allowed = {_normalised(slug.split("/")[0]) for slug in approach.routing.only}
        if _normalised(served) not in allowed:
            return replace(interpretation, outcome=Outcome.ERROR, reason="provider 不在允許清單內", payload=None, served_provider=served)
        return replace(interpretation, served_provider=served)

    @staticmethod
    def _error(status: int, error) -> Interpretation:
        code = error.get("code") if isinstance(error, dict) else None
        code = code if isinstance(code, int) and not isinstance(code, bool) else status
        metadata = error.get("metadata") if isinstance(error, dict) else None
        reasons = metadata.get("reasons") if isinstance(metadata, dict) else None
        if code == 403 and isinstance(reasons, list) and reasons:   # moderation：沒有任何譯文，不重試
            safe = [r for r in reasons[:MAX_REASONS] if isinstance(r, str) and _REASON.fullmatch(r)]
            return Interpretation(Outcome.BLOCK, "moderation:" + (",".join(safe) or "unknown"))
        if code in (408, 429) or code >= 500:   # 暫時性：交給共用的重試層
            return Interpretation(Outcome.ERROR, f"HTTP {code}", retryable=True)
        return Interpretation(Outcome.ERROR, f"HTTP {code}")
