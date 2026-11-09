"""報告共用的小工具：摘要版只放得出是代碼的字串。"""
import re

_SAFE_CODE = re.compile(r"[A-Za-z0-9_:.\-]{1,60}")   # 沒有空白：一句英文不會通過


def shown(value, summary: bool):
    """摘要版只放得出是代碼的字串（Block 原因、模型版本、baseline 標籤等：字母、數字和 _:.-，60 字以內）：provider 或檔案裡的其他文字不外傳。"""
    if not summary or value is None or _SAFE_CODE.fullmatch(str(value)):
        return value
    return "（其他）"
