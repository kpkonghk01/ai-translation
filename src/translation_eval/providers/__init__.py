"""Provider adapter：只負責組請求和把回應解讀成共同的結果類別。重試、續跑、記錄在共用的一層。"""
from typing import Protocol

from ..outcome import Interpretation
from ..schemas import ApproachConfig
from ..transport import Request, Response
from . import dashscope, gemini


class Adapter(Protocol):
    def endpoint(self, approach: ApproachConfig) -> str: ...

    def build_request(self, approach: ApproachConfig, system_prompt: str, user_text: str, api_key: str) -> Request: ...

    def interpret(self, response: Response) -> Interpretation: ...


_ADAPTERS = {"gemini": gemini.GeminiAdapter(), "dashscope": dashscope.DashscopeAdapter()}


def adapter_for(approach: ApproachConfig) -> Adapter:
    return _ADAPTERS[approach.provider]
