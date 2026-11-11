"""各 provider 的 adapter 共用的解讀結果，以及「模型輸出的文字 → 結果類別」的規則。"""
import json
import re
from dataclasses import dataclass

from .schemas import Outcome


@dataclass(frozen=True)
class Interpretation:
    outcome: Outcome
    reason: str | None = None
    payload: dict | str | None = None   # 只有 ok 才有：解析後的 JSON 物件，逐 Component 模式是譯文文字
    retryable: bool = False       # 429 與 5xx：由共用的重試層決定要不要重試
    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    model_version: str | None = None
    served_provider: str | None = None   # OpenRouter：回應寫的、實際服務這個請求的 provider
    provider_violation: bool = False     # 資料被送到不在允許清單內的 provider：優先於其他結果，Run 要立刻停止


class UnrecognisedResponse(Exception):
    """回應的某個欄位型別不符：provider 的 adapter 把它記為 error，不是讓程式拋出例外。"""


def lenient(read_meta, body) -> dict:
    """token 數和模型版本只是附帶的資料：讀不出來就沒有這些資料，但不能因此改變結果類別。
    尤其是 block：回應的其他部分怪怪的，Block 率也不能因此少算。"""
    try:
        return read_meta(body)
    except UnrecognisedResponse:
        return {}


def token_count(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UnrecognisedResponse
    return value


# 只接受最外層包了一層 code fence 的 JSON，其他偏離都算 malformed
_FENCE = re.compile(r"\A```(?:json)?[ \t]*\n(.*)\n```\Z", re.DOTALL)

# 拒絕語的樣式（英文與中文）。只看開頭的一段：譯文的引語裡也可能有 "I can't"。
_REFUSAL = re.compile(
    r"\bI(?:'m| am| ’m) (?:sorry|unable|not able)\b"
    r"|\bI (?:can[’']?t|cannot|won[’']?t|am unable|am not able)\b"
    r"|\b(?:can[’']?t|cannot|unable to) (?:help|assist|comply|translate|fulfil+|process)\b"
    r"|\bagainst (?:my|our) (?:guidelines|policies)\b"
    r"|無法(?:翻譯|協助|幫助|提供|處理)|无法(?:翻译|协助|帮助|提供|处理)|不能(?:翻譯|翻译|協助|协助)|抱歉|對不起|对不起",
    re.IGNORECASE,
)
REFUSAL_SCAN_CHARS = 400


def looks_like_refusal(text: str) -> bool:
    return bool(_REFUSAL.search(text[:REFUSAL_SCAN_CHARS]))


def opens_with_refusal(text: str) -> bool:
    """一段譯文一開頭就是拒絕語（前面的空白和引號不計）。譯文中間出現「I can't」很常見（引語），開頭就是拒絕語幾乎一定是拒絕。"""
    return bool(_REFUSAL.match(text.lstrip(" \t\r\n\"'“‘「『")))


EMPTY_REASON = "輸出是空的"


def empty_output(**meta) -> Interpretation:
    """正常結束，但沒有任何輸出：這次請求沒有拿到東西，不是模型對內容的反應（Tencent 的 Hy-MT2 對部分輸入會回 content: null）。
    和 error 一樣沒有結果，--redo-errors 會重做；它不是 malformed（輸出有，但格式不對）。"""
    return Interpretation(Outcome.EMPTY, EMPTY_REASON, **meta)


def classify_plain_text(text: str) -> tuple[Outcome, str | None, str | None]:
    """逐 Component 模式：預期的輸出就是純譯文（空的輸出在這之前已由 adapter 記為 empty）。拒絕語的字樣在這裡不判：譯文的引語裡本來就可能有
    「I can't」，只認 provider 明確給的訊號（message.refusal、content_filter）。模型若直接拿拒絕語當譯文，這個請求仍是 ok，
    但 check 會在那個 Component 列出疑似 Refusal 的警示（一開頭就是拒絕語，或遠短於原文），所以不會沒人看到。"""
    return Outcome.OK, None, text.strip()


def classify_text(text: str) -> tuple[Outcome, str | None, dict | None]:
    """模型正常結束時的輸出文字 → (結果類別, 說明, 解析後的 JSON)。

    不是預期的 JSON、而且符合拒絕語的樣式 → refusal；不是預期的 JSON、又不符合 → malformed。
    """
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    try:
        payload = json.loads(fenced.group(1) if fenced else stripped)
    except (json.JSONDecodeError, RecursionError):   # 括號層數多到解析器超出遞迴上限，也是壞掉的輸出
        attempted_json = stripped.startswith(("{", "[", "```"))   # 想輸出 JSON 但壞了，不是拒絕
        if attempted_json:
            return Outcome.MALFORMED, "輸出不是 JSON", None
        return refusal_or_malformed(stripped, "輸出不是 JSON")
    if not isinstance(payload, dict):   # 例如把拒絕語包成 JSON 字串：responseMimeType 要求 JSON，拒絕也常是 JSON
        return refusal_or_malformed(stripped, "輸出的 JSON 不是物件")
    return Outcome.OK, None, payload


def refusal_or_malformed(text: str, malformed_reason: str) -> tuple[Outcome, str | None, None]:
    """不是預期的 JSON 時：符合拒絕語的樣式是 refusal，否則是 malformed。"""
    if looks_like_refusal(text):
        return Outcome.REFUSAL, "輸出是拒絕語", None
    return Outcome.MALFORMED, malformed_reason, None
