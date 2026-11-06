"""翻譯 prompt 範本：每個 Direction 一份，所有 Approach 共用，以保證比較公平。

範本的 id 帶版本（改範本就要改 id），Run 紀錄存 id 與檔案內容的 hash。
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .schemas import Direction

_DIR = Path(__file__).parent / "prompt_templates"
_TEMPLATE_IDS = {Direction.TRAD_TO_EN: "translate-trad-to-en.v1"}


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    text: str
    hash: str


def translation_template(direction: Direction) -> PromptTemplate:
    template_id = _TEMPLATE_IDS[direction]
    raw = (_DIR / f"{template_id}.txt").read_bytes()
    return PromptTemplate(id=template_id, text=raw.decode("utf-8"), hash=hashlib.sha256(raw).hexdigest())
