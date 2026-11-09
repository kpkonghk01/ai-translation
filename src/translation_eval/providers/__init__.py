"""Provider adapter：只負責組請求和把回應解讀成共同的結果類別。重試、續跑、記錄在共用的一層。"""
from typing import Protocol

from ..outcome import Interpretation
from ..schemas import ApproachConfig
from ..transport import Request, Response
from . import dashscope, gemini, openrouter


class Adapter(Protocol):
    def endpoint(self, approach: ApproachConfig) -> str: ...

    def build_request(self, approach: ApproachConfig, system_prompt: str, user_text: str, api_key: str) -> Request: ...

    def interpret(self, response: Response, approach: ApproachConfig, classify=...) -> Interpretation: ...

    def problems(self, approach: ApproachConfig) -> list[str]:
        """送出任何請求之前就能發現的設定問題（例如 OpenRouter 的允許清單還沒確認）。"""
        ...

    def describe(self, approach: ApproachConfig) -> list[str]:
        """--dry-run 要多印出的行（例如 OpenRouter 的 routing）。"""
        ...


_ADAPTERS = {"gemini": gemini.GeminiAdapter(), "dashscope": dashscope.DashscopeAdapter(), "openrouter": openrouter.OpenRouterAdapter()}


def adapter_for(approach: ApproachConfig) -> Adapter:
    return _ADAPTERS[approach.provider]
