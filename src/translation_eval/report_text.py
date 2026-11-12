"""報告共用的小工具：摘要版只放得出是代碼的字串、附分子分母的比率，和展示報告、審閱清單共用的顯示字串（方向、題材組別、淡化類型）。"""
import re

from .schemas import Direction, Domain, WeakeningType

DIRECTION_LABELS = {Direction.TRAD_TO_EN: "繁→英", Direction.TRAD_TO_SIMPLIFIED: "繁→簡"}
GROUP_LABELS = {Domain.GENERAL.value: "一般題材", Domain.CHINA_POLITICAL.value: "中國政治題材"}
# 淡化類型的白話說明（定義見 GLOSSARY 的 Softening）：給不看類型代號的人看
WEAKENING_LABELS = {
    WeakeningType.A: "施害者或行為主體被拿掉",
    WeakeningType.B: "用詞被改得委婉或中性",
    WeakeningType.C: "數量被寫得模糊",
    WeakeningType.D: "具體細節被刪掉或概括",
    WeakeningType.E: "對原文斷言的事加上限定（可能、據稱之類）",
    WeakeningType.F: "直接引語裡的粗話或狠話被洗乾淨",
    WeakeningType.AMPLIFICATION: "比原文更聳動",
}

_SAFE_CODE = re.compile(r"[A-Za-z0-9_:.\-]{1,60}")   # 沒有空白：一句英文不會通過


def shown(value, summary: bool):
    """摘要版只放得出是代碼的字串（Block 原因、模型版本、baseline 標籤等：字母、數字和 _:.-，60 字以內）：provider 或檔案裡的其他文字不外傳。"""
    if not summary or value is None or _SAFE_CODE.fullmatch(str(value)):
        return value
    return "（其他）"


def group_label(group: str) -> str:
    return GROUP_LABELS.get(group, group)


def ordered_groups(groups) -> list[str]:
    """題材組別的顯示順序：已知的（一般、中國政治）在前，其餘（group_overrides 的自訂組名）照名稱排。"""
    known = list(GROUP_LABELS)
    return sorted(set(groups), key=lambda g: (known.index(g) if g in known else len(known), g))


def ratio(numerator: int, denominator: int) -> str:
    """每個比率都附分子和分母：3/10（30.0%）；分母是 0 就沒有百分比。"""
    if denominator == 0:
        return f"{numerator}/{denominator}（—）"
    return f"{numerator}/{denominator}（{100 * numerator / denominator:.1f}%）"
