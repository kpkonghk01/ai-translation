"""假的 HTTP transport：按預先寫好的回應依次回傳，並記錄收到的請求供測試檢查。

回應可以是 Response（正常回傳）或例外（模擬 timeout 等網絡失敗）。預先寫好的回應用完後再收到請求，
就令測試失敗：多送了一個沒預期的請求，通常是重試或續跑出了錯。
"""
from translation_eval.transport import Request, Response


class FakeTransport:
    def __init__(self, *scripted):
        self._scripted = list(scripted)
        self.requests: list[Request] = []

    def send(self, request: Request) -> Response:
        self.requests.append(request)
        if not self._scripted:
            raise AssertionError(f"沒有預期的請求：{request.method} {request.url}")
        item = self._scripted.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FunctionTransport:
    """回應由函式決定（看請求的內容）：切塊與逐 Component 的請求數量事先不固定，沒辦法預先寫好。"""

    def __init__(self, reply):
        self._reply = reply
        self.requests: list[Request] = []

    def send(self, request: Request) -> Response:
        self.requests.append(request)
        item = self._reply(request)
        if isinstance(item, BaseException):
            raise item
        return item
