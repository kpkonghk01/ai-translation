"""Deterministic check 的文字規則：只做字串運算，不認識 Run、Component 或檔案。同一輸入必得同一結果。"""
import functools
import math
import re
from dataclasses import dataclass

from opencc import OpenCC


def squash(text: str) -> str:
    """去掉所有空白。原文偶有多餘的空白，比對和計長度時都忽略。"""
    return re.sub(r"\s+", "", text)


def quoted(text: str) -> str:
    """Markdown 的引用區塊（接在「- 標籤：」後面）：每一行都加 >，原文與譯文放進清單用。"""
    return "\n  > " + text.replace("\n", "\n  > ")


def nonspace_length(text: str) -> int:
    return len(squash(text))


# 漢字：〇（數字零，二〇二四年的一部分）、CJK 統一表意文字（含擴展 A 到 I）與相容表意文字。全形標點不是漢字，不在內。
_HAN_RUN = re.compile("[〇㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f\U00030000-\U000323af]+")


def han_runs(text: str) -> list[str]:
    """文字裡每一段連續的漢字，依出現順序。同一段出現兩次就列兩次。"""
    return _HAN_RUN.findall(text)


def contains_han(text: str) -> bool:
    return bool(_HAN_RUN.search(text))


@functools.cache
def _t2s() -> OpenCC:
    return OpenCC("t2s")


def _changed_characters(text: str) -> list[str]:
    """OpenCC t2s 會改動的字，依在文字裡出現的順序。要轉換整段，不能逐字：詞組詞典會保留某些詞裡的繁體字
    （乾隆、乾坤的「乾」），逐字判斷會把它們誤當成殘留。"""
    converted = _t2s().convert(text)
    if len(converted) == len(text):   # t2s 的詞組與字都是同長度地轉換（本機 6089 段文字沒有例外），所以可以逐位置比對
        return [before for before, after in zip(text, converted) if before != after]
    # 長度變了就沒辦法對齊：改用逐字判斷。寧可多報（只是 minor 的 Finding），也不能漏報
    return [char for char in text if _t2s().convert(char) != char]


def residual_traditional(text: str, exempt: frozenset[str] = frozenset()) -> list[str]:
    """文字裡 OpenCC t2s 會改動的字，即殘留的繁體字。每個字只列一次，依第一次出現的順序；exempt 內的字不列。"""
    residue: list[str] = []
    for char in _changed_characters(text):
        if char not in residue and char not in exempt:
            residue.append(char)
    return residue


# ───────────── 數字 ─────────────

_DIGIT_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")


def digit_strings(text: str) -> set[str]:
    """文字裡的阿拉伯數字（去掉千分位的逗號）。"""
    return {token.replace(",", "") for token in _DIGIT_TOKEN.findall(text)}


_CHINESE_QUANTITY = re.compile(r"([零〇一二兩三四五六七八九十百千萬億]+)(?=[名人宗個年月日歲次元倍成間件項位起批度])")


def chinese_numbers(text: str) -> set[str]:
    """文字裡接著量詞或單位的中文數字（三名、十年、兩宗）。「兩」與「二」視為同一個。沒有量詞的（統一、唯一）不算，免得把詞的一部分當數字。"""
    return {token.replace("兩", "二") for token in _CHINESE_QUANTITY.findall(text)}


def missing_digit_tokens(source_text: str, translated_text: str) -> list[str]:
    """繁→簡：原文的阿拉伯數字（去掉千位分隔號）在譯文找不到的。單位 萬／万 這類只是字形不同，不用換算。"""
    def normalised(text: str) -> list[str]:
        return [t.replace(",", "") for t in _DIGIT_TOKEN.findall(text)]
    present = set(normalised(translated_text))
    return [t for t in normalised(source_text) if t not in present]

# 原文的數字：數字加上可選的 千／百 和 萬／億；後面接「月」的是月份
_SOURCE_GROUP = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([千仟百佰])?\s*([萬万億亿])?(月)?")
_SMALL_UNIT = {"千": 1e3, "仟": 1e3, "百": 1e2, "佰": 1e2}
_BIG_UNIT = {"萬": 1e4, "万": 1e4, "億": 1e8, "亿": 1e8}
_TRANSLATED_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+")
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
    value: float        # 已把 千、百、萬、億 換算
    month: int | None   # 「5月」這樣的寫法：譯文寫月份名也算找到


@dataclass
class _Group:
    start: int
    end: int
    value: float
    big_unit: str | None
    month: int | None


def _continues(previous_unit: str | None, unit: str | None) -> bool:
    """緊接著的兩段數字是同一個數：1億2000萬、3萬5000。億 後面只接 萬；萬 後面接沒有單位的數。
    億 後面直接接沒有單位的數（1億5000）有歧義，不合併。"""
    if previous_unit is None:
        return False
    return (previous_unit in "億亿" and unit is not None and unit in "萬万") or (previous_unit in "萬万" and unit is None)


def source_numbers(text: str) -> list[SourceNumber]:
    """原文的數字：去掉千位分隔號，千、百、萬、億 換算成數值；1億2000萬 這樣緊接的寫法合成一個數。"""
    groups: list[_Group] = []
    for m in _SOURCE_GROUP.finditer(text):
        digits, small, big, month_mark = m.groups()
        try:
            plain = float(digits.replace(",", ""))
        except ValueError:
            continue
        is_month = bool(month_mark) and not small and not big and plain.is_integer() and 1 <= plain <= 12
        group = _Group(m.start(), m.end(), plain * _SMALL_UNIT.get(small, 1) * _BIG_UNIT.get(big, 1), big, int(plain) if is_month else None)
        previous = groups[-1] if groups else None
        if previous and group.start == previous.end and group.month is None and not month_mark and _continues(previous.big_unit, big):
            previous.value += group.value
            previous.end = group.end
            previous.big_unit = big or previous.big_unit
        else:
            groups.append(group)
    return [SourceNumber(text[g.start:g.end].strip(), g.value, g.month) for g in groups]


def _read_number(tokens: list[str], i: int) -> tuple[float | None, int]:
    """從 tokens[i] 起讀一個數：數字、12、twenty five、third、a（後面接 hundred 之類）。回傳 (數值, 用掉幾個 token)。"""
    token = tokens[i]
    if token[0].isdigit():
        return float(token.replace(",", "")), 1
    if token in _TENS:
        if i + 1 < len(tokens) and 1 <= _ONES.get(tokens[i + 1], 0) <= 9:   # twenty-five 拆成 twenty、five
            return float(_TENS[token] + _ONES[tokens[i + 1]]), 2
        return float(_TENS[token]), 1
    if token in _ONES:
        return float(_ONES[token]), 1
    if token in _ORDINALS:   # 第3名 → third
        return float(_ORDINALS[token]), 1
    if token in ("a", "an") and i + 1 < len(tokens) and tokens[i + 1] in _MAGNITUDE:
        return 1.0, 1
    return None, 0


def _translated_values(tokens: list[str]) -> set[float]:
    """譯文裡寫成數字或文字的數值：15、1,500、twelve、twenty-five、15 million、two hundred and fifty。"""
    values: set[float] = set()
    i = 0
    while i < len(tokens):
        base, used = _read_number(tokens, i)
        if base is None:
            i += 1
            continue
        values.add(base)
        j = i + used
        scaled, last_magnitude = base, None
        while j < len(tokens) and tokens[j] in _MAGNITUDE:
            last_magnitude = tokens[j]
            scaled *= _MAGNITUDE[last_magnitude]
            values.add(scaled)
            j += 1
        if last_magnitude == "hundred":   # two hundred (and) fifty
            k = j + 1 if j < len(tokens) and tokens[j] == "and" else j
            tail, tail_used = _read_number(tokens, k) if k < len(tokens) else (None, 0)
            if tail is not None and tail < 100:
                values.add(scaled + tail)
                j = k + tail_used
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


def quoted_phrases(text: str) -> set[str]:
    """中文引號（「」『』“”）最外層的一對裡的文字（至少 2 字），忽略空白；巢狀的引號留在外層那一段裡。沒有閉合的不算。"""
    phrases, depth, start = set(), 0, 0
    for i, ch in enumerate(text):
        if ch in _QUOTE_OPEN:
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch in _QUOTE_CLOSE and depth:
            depth -= 1
            if depth == 0 and len(squash(text[start:i])) >= 2:
                phrases.add(squash(text[start:i]))
    return phrases
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
