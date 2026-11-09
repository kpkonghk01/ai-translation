"""providers：列出 OpenRouter 上某個模型的 endpoint 和 provider，讓 Pan 確認允許清單。

用法：
  uv run --locked python -I -m translation_eval providers <作者/名稱> [--dry-run]

需要 OPENROUTER_API_KEY，會呼叫 API，所以只由 Pan 在自己的終端機執行。指令本身不寫入任何東西，也不改設定：
Pan 看過清單、排除位於中國大陸的 provider 之後，自己把要用的 tag 寫進 Approach 設定的 routing.only，並寫上
routing.confirmed_on。終端只印 provider 的名稱、tag 和 API 給的幾個欄位，不印任何文章內容。
"""
import json
import os
import re

from .errors import InputError, StoppedAfterSending
from .schemas import PROVIDER_KEY_ENV
from .transport import Request, Transport, TransportFailure

KEY_ENV = PROVIDER_KEY_ENV["openrouter"]
LISTING_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:-]*")
_SAFE_TEXT = re.compile(r"[\w .:/()+-]{1,60}")
# API 文件列出的欄位；其他欄位裡名字像「地區或資料政策」的，也印出來，讓 Pan 看到 API 實際提供了什麼
_DOCUMENTED = {"provider_name", "tag", "name", "model_id", "model_name", "context_length", "max_completion_tokens",
               "max_prompt_tokens", "pricing", "quantization", "status", "supported_parameters", "supports_implicit_caching",
               "supports_diarization", "uptime_last_5m", "uptime_last_30m", "uptime_last_1d", "latency_last_30m",
               "throughput_last_30m"}
_POLICY_LIKE = re.compile(r"region|location|country|datacenter|data|policy|retention|training|zdr|moderat", re.IGNORECASE)


def _safe(value) -> str:
    text = str(value)
    return text if isinstance(value, (str, int, float, bool)) and _SAFE_TEXT.fullmatch(text) else "(格式不認識)"


def _policy_fields(endpoint: dict) -> list[str]:
    out = []
    for key, value in endpoint.items():
        if key in _DOCUMENTED or not _POLICY_LIKE.search(key):
            continue
        out.append(f"{_safe(key)}={_safe(value)}" if isinstance(value, (str, int, float, bool)) else f"{_safe(key)}（有內容，這裡不展開）")
    return out


def handle(args, transport: Transport) -> int:
    if not _MODEL.fullmatch(args.model):
        raise InputError("model 要寫成「作者/名稱」，例如 tencent/hy-mt2")
    url = LISTING_URL.format(model=args.model)
    if args.dry_run:
        print("dry-run：不送出任何請求")
        print(f"endpoint：{url}")
        return 0
    key = os.environ.get(KEY_ENV)
    if not key:
        raise InputError(f"缺少環境變數 {KEY_ENV}")
    try:
        response = transport.send(Request("GET", url, {"Authorization": f"Bearer {key}"}, b"", 30))
    except TransportFailure as e:
        raise StoppedAfterSending(f"連線失敗：{str(e).replace(key, '[REDACTED]')}") from e
    if response.status != 200:
        raise StoppedAfterSending(f"OpenRouter 回應 HTTP {response.status}")
    try:
        body = json.loads(response.body)
    except (ValueError, RecursionError) as e:
        raise StoppedAfterSending("OpenRouter 的回應不是 JSON") from e
    data = body.get("data") if isinstance(body, dict) else None
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, list) or not endpoints or not all(isinstance(e, dict) for e in endpoints):
        raise StoppedAfterSending("OpenRouter 的回應沒有任何 endpoint，或格式不認識")

    print(f"{args.model}：{len(endpoints)} 個 endpoint")
    any_policy = False
    for endpoint in endpoints:
        details = [f"quantization={_safe(endpoint.get('quantization', '未寫'))}", f"status={_safe(endpoint.get('status', '未寫'))}",
                   f"context_length={_safe(endpoint.get('context_length', '未寫'))}"]
        policy = _policy_fields(endpoint)
        any_policy = any_policy or bool(policy)
        print(f"- {_safe(endpoint.get('provider_name', '未寫'))}（only 用：{_safe(endpoint.get('tag', '未寫'))}）：" + "、".join(details + policy))
    if not any_policy:
        print("API 沒有提供地區或資料政策的欄位，請到 OpenRouter 的模型頁確認各 provider 所在的地區與資料政策。")
    print("確認後，把要用的 tag 寫進 Approach 設定的 routing.only，並寫上 routing.confirmed_on（確認日期）；排除位於中國大陸的 provider。")
    return 0
