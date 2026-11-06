"""各 provider 的 adapter 共用的解讀結果，以及「模型輸出的文字 → 結果類別」的規則。"""
import json
import re
from dataclasses import dataclass

from .schemas import Outcome


@dataclass(frozen=True)
class Interpretation:
    outcome: Outcome
    reason: str | None = None
    payload: dict | None = None   # 只有 ok 才有：解析後的 JSON 物件
    retryable: bool = False       # 429 與 5xx：由共用的重試層決定要不要重試
    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    model_version: str | None = None


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


def classify_text(text: str) -> tuple[Outcome, str | None, dict | None]:
    """模型正常結束時的輸出文字 → (結果類別, 說明, 解析後的 JSON)。

    不是預期的 JSON、而且符合拒絕語的樣式 → refusal；不是預期的 JSON、又不符合 → malformed。
    """
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    try:
        payload = json.loads(fenced.group(1) if fenced else stripped)
    except json.JSONDecodeError:
        attempted_json = stripped.startswith(("{", "[", "```"))   # 想輸出 JSON 但壞了，不是拒絕
        if not attempted_json and looks_like_refusal(stripped):
            return Outcome.REFUSAL, "輸出是拒絕語", None
        return Outcome.MALFORMED, "輸出不是 JSON", None
    if not isinstance(payload, dict):
        return Outcome.MALFORMED, "輸出的 JSON 不是物件", None
    return Outcome.OK, None, payload
