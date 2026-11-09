"""翻譯 prompt 範本：每個 Direction 一份，所有 Approach 共用，以保證比較公平。

範本只寫 spec 要求的結構、語域和 Term List，不加任何防淡化的指示：要看的是模型本身的反應。
Run 紀錄存範本的 id 和檔案內容的 hash，hash 才是範本內容的身分，所以 id 不帶版本號。
範本以 TERMINOLOGY_MARKER 分成兩段：之前是固定的指示；之後是只在文章含有 Term List 條目時才加上的一節，
其中的 {terms} 換成條目。
"""
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .schemas import Direction, RequestMode, TermEntry

_DIR = Path(__file__).parent / "prompt_templates"
_TEMPLATE_IDS = {
    (Direction.TRAD_TO_EN, RequestMode.ARTICLE_JSON): "translate-trad-to-en",
    (Direction.TRAD_TO_SIMPLIFIED, RequestMode.ARTICLE_JSON): "translate-trad-to-simplified",
    (Direction.TRAD_TO_EN, RequestMode.PER_COMPONENT): "translate-trad-to-en-component",
    (Direction.TRAD_TO_SIMPLIFIED, RequestMode.PER_COMPONENT): "translate-trad-to-simplified-component",
}
TERMINOLOGY_MARKER = "<<TERMINOLOGY>>"


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    text: str
    hash: str

    def render(self, entries: Sequence[TermEntry], direction: Direction) -> str:
        """entries 是這篇文章原文出現的條目；沒有條目就不加 Terminology 一節。"""
        base, _, terminology = self.text.partition(TERMINOLOGY_MARKER)
        if not entries:
            return base.rstrip() + "\n"
        lines = "\n".join(f"- {e.source} -> {' / '.join(e.renderings(direction))}" for e in entries)
        return base.rstrip() + "\n\n" + terminology.strip().replace("{terms}", lines) + "\n"


def translation_template(direction: Direction, request_mode: RequestMode) -> PromptTemplate:
    template_id = _TEMPLATE_IDS[(direction, request_mode)]
    raw = (_DIR / f"{template_id}.txt").read_bytes()
    return PromptTemplate(id=template_id, text=raw.decode("utf-8"), hash=hashlib.sha256(raw).hexdigest())
