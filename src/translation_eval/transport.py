"""唯一的 HTTP transport：所有對外呼叫都經過這裡，測試換成假的 transport。

這是 package 內唯一可以 import 網絡相關庫的模組（tools/check_package_rules.py 會檢查）。
真的 transport 有三道防線：設了 AI_TRANSLATION_OFFLINE 就拒絕送出（tools/check.sh 會設）；只送 https；
不跟隨轉址，因為 urllib 轉址時會把自訂的 key header 一併帶去別的主機。
"""
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

OFFLINE_ENV = "AI_TRANSLATION_OFFLINE"


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    timeout_seconds: float


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class TransportForbidden(Exception):
    """transport 拒絕送出：這是設定或環境的問題，不是某個 Run 的結果，所以不重試、不記成 error。"""


class TransportFailure(Exception):
    """網絡層失敗（連不上、中途斷線）。可以重試。"""


class TransportTimeout(TransportFailure):
    pass


class Transport(Protocol):
    def send(self, request: Request) -> Response: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class UrllibTransport:
    def send(self, request: Request) -> Response:
        if OFFLINE_ENV in os.environ:
            raise TransportForbidden(f"已設定 {OFFLINE_ENV}：離線模式不送出任何請求。測試要注入假的 transport")
        if not request.url.startswith("https://"):
            raise TransportForbidden("只允許 https 的網址")
        req = urllib.request.Request(request.url, data=request.body, headers=request.headers, method=request.method)
        try:
            with _OPENER.open(req, timeout=request.timeout_seconds) as resp:
                return Response(resp.status, dict(resp.headers.items()), resp.read())
        except urllib.error.HTTPError as e:   # 4xx、5xx，以及沒有跟隨的 3xx：照樣交給 adapter 解讀
            with e:
                return Response(e.code, dict(e.headers.items()), e.read())
        except TimeoutError as e:
            raise TransportTimeout("timeout") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise TransportTimeout("timeout") from e
            raise TransportFailure(f"連線失敗：{e.reason}") from e
        except OSError as e:
            raise TransportFailure(f"連線失敗：{type(e).__name__}") from e
