"""Deterministic check 的文字規則：只做字串運算，不認識 Run、Component 或檔案。同一輸入必得同一結果。"""
import math
import re
from dataclasses import dataclass

# 漢字：CJK 統一表意文字（含擴展 A 及以後）與相容表意文字。全形標點不是漢字，不在內。
_HAN_RUN = re.compile("[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]+")


def han_runs(text: str) -> list[str]:
    """文字裡每一段連續的漢字，依出現順序。同一段出現兩次就列兩次。"""
    return _HAN_RUN.findall(text)


def contains_han(text: str) -> bool:
    return bool(_HAN_RUN.search(text))


def nonspace_length(text: str) -> int:
    return len("".join(text.split()))


# ───────────── 數字 ─────────────

_SOURCE_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(萬|万|億|亿)?(月)?")
_CHINESE_MAGNITUDE = {"萬": 1e4, "万": 1e4, "億": 1e8, "亿": 1e8}
_TRANSLATED_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+(?:-[a-z]+)?")
# 新聞英文（AP 式）會把小的數字寫成文字、把「5月」寫成月份名，所以這些都算找到了
_ONES = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
_MAGNITUDE = {"hundred": 100, "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_MONTHS = {name: n for n, names in enumerate(
    ("january jan", "february feb", "march mar", "april apr", "may", "june jun", "july jul", "august aug",
     "september sept sep", "october oct", "november nov", "december dec"), 1) for name in names.split()}


@dataclass(frozen=True)
class SourceNumber:
    token: str          # 原文的寫法，警示用它說明是哪個數字
    value: float        # 已把 萬、億 換算
    month: int | None   # 「5月」這樣的寫法：譯文寫月份名也算找到


def source_numbers(text: str) -> list[SourceNumber]:
    """原文的數字：去掉千位分隔號，萬、億 換算成數值。"""
    out = []
    for m in _SOURCE_NUMBER.finditer(text):
        digits, magnitude, month_mark = m.groups()
        try:
            value = float(digits.replace(",", ""))
        except ValueError:
            continue
        is_month = bool(month_mark) and not magnitude and value.is_integer() and 1 <= value <= 12
        out.append(SourceNumber(m.group(0).strip(), value * _CHINESE_MAGNITUDE.get(magnitude, 1), int(value) if is_month else None))
    return out


def _word_value(token: str) -> int | None:
    if token in _ONES:
        return _ONES[token]
    if token in _ORDINALS:   # 第3名 → third
        return _ORDINALS[token]
    if token in _TENS:
        return _TENS[token]
    head, _, tail = token.partition("-")
    if head in _TENS and _ONES.get(tail, 0) >= 1 and _ONES[tail] <= 9:   # twenty-five
        return _TENS[head] + _ONES[tail]
    return None


def _translated_values(tokens: list[str]) -> set[float]:
    """譯文裡寫成數字或文字的數值：15、1,500、twelve、twenty-five、15 million、two hundred and fifty。"""
    values: set[float] = set()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token[0].isdigit():
            base, used = float(token.replace(",", "")), 1
        elif _word_value(token) is not None:
            base, used = float(_word_value(token)), 1
        elif token in ("a", "an") and i + 1 < len(tokens) and tokens[i + 1] in _MAGNITUDE:
            base, used = 1.0, 1
        else:
            i += 1
            continue
        values.add(base)
        j = i + used
        if j < len(tokens) and tokens[j] in _MAGNITUDE:
            scaled = base * _MAGNITUDE[tokens[j]]
            values.add(scaled)
            j += 1
            if tokens[j - 1] == "hundred":
                k = j + 1 if j < len(tokens) and tokens[j] == "and" else j
                if k < len(tokens) and _word_value(tokens[k]) is not None:
                    values.add(scaled + _word_value(tokens[k]))
                    j = k + 1
        i = max(j, i + 1)
    return values


def missing_numbers(source_text: str, translated_text: str) -> list[str]:
    """原文的數字（依出現順序，以原文的寫法）裡，譯文找不到的。"""
    tokens = _TRANSLATED_TOKEN.findall(translated_text.lower())
    values = _translated_values(tokens)
    months = {_MONTHS[t] for t in tokens if t in _MONTHS}
    missing = []
    for n in source_numbers(source_text):
        if n.value == 1 and n.month is None:   # 1 在英文常寫成冠詞 a／an（1名男子 → a man），沒辦法用字串判斷，不列警示
            continue
        if any(math.isclose(v, n.value, rel_tol=1e-9) for v in values) or (n.month is not None and n.month in months):
            continue
        missing.append(n.token)
    return missing


# ───────────── 直接引語 ─────────────

_QUOTE_OPEN, _QUOTE_CLOSE = "「『“", "」』”"
_SINGLE_QUOTES = "'‘’"


def _quote_role(text: str, i: int, straight_open: bool, singles_open: int) -> str | None:
    """text[i] 是開引號、閉引號，還是不是引號。單引號要看前後文：it's、workers' 的撇號不算。"""
    ch = text[i]
    if ch in _QUOTE_OPEN:
        return "open"
    if ch in _QUOTE_CLOSE:
        return "close"
    if ch == '"':   # 直引號開和關是同一個字元，靠奇偶判斷
        return "close" if straight_open else "open"
    if ch in _SINGLE_QUOTES:
        before = text[i - 1] if i > 0 else " "
        after = text[i + 1] if i + 1 < len(text) else " "
        if ch == "‘" or (not before.isalnum() and after.isalnum()):
            return "open"
        if singles_open and not after.isalnum():
            return "close"
    return None


def quote_count(text: str) -> int:
    """直接引語的數量：最外層的一對引號算一個，巢狀的不另計。中英文的引號用同一套規則，才能互相比較；
    英文譯文常用單引號（例如標題），所以單引號也算。"""
    depth = count = singles_open = 0
    straight_open = False
    for i, ch in enumerate(text):
        role = _quote_role(text, i, straight_open, singles_open)
        if ch == '"':
            straight_open = role == "open"
        if ch in _SINGLE_QUOTES and role:
            singles_open += 1 if role == "open" else -1
        if role == "open":
            if depth == 0:
                count += 1
            depth += 1
        elif role == "close":
            depth = max(0, depth - 1)
    return count
