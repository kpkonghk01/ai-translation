"""送出一個請求並解讀回應：429、5xx 和 timeout 以退避方式重試，每次嘗試都記錄。

重試放在這一層，不在各個 provider adapter 重複實作。Block 和 refusal 不重試：重複嘗試要靠增加 Run 次數，
否則 Block 率就不是單次嘗試的結果。
"""
import time
from dataclasses import dataclass, replace

from .outcome import Interpretation
from .providers import Adapter
from .schemas import Attempt, Outcome, RetryConfig
from .transport import Request, Transport, TransportFailure

REDACTED = "[REDACTED]"


@dataclass
class Exchange:
    interpretation: Interpretation
    raw_body: bytes | None   # 最後一次嘗試的 response body；最後一次是 timeout 等沒有回應的失敗時為 None
    attempts: list[Attempt]


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


def execute(transport: Transport, adapter: Adapter, request: Request, retry: RetryConfig, secrets: tuple[str, ...] = ()) -> Exchange:
    """secrets 是這個請求用到的 key。即使 transport 的錯誤訊息或伺服器的回應把 key 帶了回來，
    記下來的說明和原始回應也不會含有它。"""
    attempts: list[Attempt] = []
    for n in range(1, retry.max_attempts + 1):
        started = time.monotonic()
        try:
            response = transport.send(request)
        except TransportFailure as e:
            result, raw, status = Interpretation(Outcome.ERROR, _redact(str(e), secrets), retryable=True), None, None
        else:
            result = adapter.interpret(response)
            if result.reason:
                result = replace(result, reason=_redact(result.reason, secrets))
            raw = _redact(response.body.decode("utf-8", errors="surrogateescape"), secrets).encode("utf-8", errors="surrogateescape")
            status = response.status
        attempts.append(Attempt(http_status=status, outcome=result.outcome, reason=result.reason,
                                latency_seconds=time.monotonic() - started))
        if not result.retryable or n == retry.max_attempts:
            return Exchange(result, raw, attempts)
        time.sleep(retry.base_delay_seconds * 2 ** (n - 1))
    raise AssertionError("max_attempts 至少是 1")
