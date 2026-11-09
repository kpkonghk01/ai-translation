"""所有檔案格式的 pydantic model：設定、文章、Run 紀錄。其他模組引用這裡，不重抄欄位或 enum。

設定與紀錄都禁止多出來的欄位（拼錯欄位名會令驗證失敗，而不是被靜默忽略）。
"""
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .textrules import squash

# 會用在檔名和網址路徑裡的 id：只允許安全字元，避免設定檔把輸出寫到別的目錄
Slug = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=64)]
ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=100)]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Direction(StrEnum):
    TRAD_TO_EN = "trad-to-en"
    TRAD_TO_SIMPLIFIED = "trad-to-simplified"




class InputVariant(StrEnum):
    FULL = "full"   # 整篇
    MAIN_ONLY = "main-only"   # 只有 Main component，用於 Block 誘因實驗


class Outcome(StrEnum):
    OK = "ok"
    BLOCK = "block"
    REFUSAL = "refusal"
    MALFORMED = "malformed"
    TRUNCATED = "truncated"
    ERROR = "error"


class Dimension(StrEnum):
    ACCURACY = "Accuracy"
    TERMINOLOGY = "Terminology"
    FLUENCY = "Fluency"
    FIDELITY_OF_FORCE = "Fidelity-of-force"
    LOCALE_FORMAT = "Locale & Format"


class Severity(StrEnum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ───────────── 設定 ─────────────

PROVIDER_KEY_ENV = {"gemini": "GEMINI_API_KEY"}


class RetryConfig(StrictModel):
    max_attempts: int = Field(default=4, ge=1)
    base_delay_seconds: float = Field(default=2.0, ge=0)


class ApproachConfig(StrictModel):
    id: Slug
    family: str = Field(min_length=1)
    provider: Literal["gemini"]
    model: ModelName
    api_key_env: str
    request_mode: Literal["article-json"] = "article-json"
    temperature: float | None = Field(default=None, ge=0, le=2)   # None：不送，用 API 預設
    max_output_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: float = Field(default=600, gt=0)
    retry: RetryConfig = RetryConfig()
    concurrency: int = 1

    @model_validator(mode="after")
    def _require_own_key_env_and_serial_run(self):
        # key 只能送去它自己的 provider，不能把別家的 key 指給這個 endpoint
        if self.api_key_env != PROVIDER_KEY_ENV[self.provider]:
            raise ValueError(f"provider {self.provider} 的 api_key_env 必須是 {PROVIDER_KEY_ENV[self.provider]}")
        if self.concurrency != 1:
            raise ValueError("目前只支援循序執行（concurrency 必須是 1）")
        return self


class ArticleSet(StrictModel):
    dir: str
    ids: list[Slug] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self):
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("ids 有重覆的文章 id")
        return self


class ApproachEntry(StrictModel):
    config: str
    runs: int = Field(ge=1)
    input_variants: list[InputVariant] = Field(default=[InputVariant.FULL], min_length=1)

    @model_validator(mode="after")
    def _unique_variants(self):
        if len(set(self.input_variants)) != len(self.input_variants):
            raise ValueError("input_variants 有重覆")
        return self


class LengthRatioRange(StrictModel):
    """譯文與原文長度（不計空白的字元數）之比的正常範圍；超出就列警示。"""
    min: float = Field(gt=0)
    max: float

    @model_validator(mode="after")
    def _min_below_max(self):
        if self.min >= self.max:
            raise ValueError("min 必須小於 max")
        return self


class BaselineEntry(StrictModel):
    """方案 E（Current service）這類不經 API 的 baseline：Run 由 import-baseline 從目錄匯入，不由 translate 產生。"""
    id: Slug
    dir: str
    direction: Direction


class ExperimentConfig(StrictModel):
    id: Slug
    articles: ArticleSet
    directions: list[Direction] = Field(min_length=1)
    approaches: list[ApproachEntry] = Field(min_length=1)
    baselines: list[BaselineEntry] = []
    term_list: str | None = None   # Term List 檔的路徑；不設就不注入、也不做 Terminology 檢查
    length_ratio: dict[Direction, LengthRatioRange] = {}   # 覆寫預設的長度比例範圍（見 config.py）

    @model_validator(mode="after")
    def _directions_and_baselines_are_consistent(self):
        if len(set(self.directions)) != len(self.directions):
            raise ValueError("directions 有重覆")
        ids = [b.id for b in self.baselines]
        if len(set(ids)) != len(ids):
            raise ValueError("baselines 有重覆的 id")
        if any(b.direction not in self.directions for b in self.baselines):   # 否則報告的開頭不會列出這個 Direction
            raise ValueError("baselines 的 direction 必須是 directions 之一")
        return self


# ───────────── Term List ─────────────

class TermCategory(StrEnum):
    PERSON = "person"
    PLACE = "place"
    ORGANIZATION = "organization"
    LEGAL = "legal"
    NEWS_IDIOM = "news-idiom"


class TermEntry(StrictModel):
    source: NonBlank   # 原文詞
    translations: dict[Direction, Annotated[list[NonBlank], Field(min_length=1)]]   # 各 Direction 的允許譯法
    category: TermCategory
    severity: Severity = Severity.MINOR   # 譯文找不到任何允許的譯法時的 Severity
    simplified_exceptions: list[NonBlank] = []   # 繁→簡殘留字檢查的例外
    note: str = ""

    @model_validator(mode="after")
    def _at_least_one_direction(self):
        if not self.translations:
            raise ValueError("translations 至少要有一個 Direction")
        return self

    def applies_to(self, direction: Direction) -> bool:
        return direction in self.translations

    def renderings(self, direction: Direction) -> list[str]:
        """這個 Direction 允許的譯法；只能對 applies_to 的 Direction 取。"""
        return self.translations[direction]


class TermList(StrictModel):
    terms: list[TermEntry]

    @model_validator(mode="after")
    def _no_duplicate_sources(self):
        # 只報位置，不抄條目的文字；連空白不同的寫法也算重覆
        first_seen: dict[str, int] = {}
        duplicates = []
        for n, entry in enumerate(self.terms):
            squashed = squash(entry.source)
            if squashed in first_seen:
                duplicates.append(f"terms[{n}] 與 terms[{first_seen[squashed]}] 的原文詞重覆")
            first_seen.setdefault(squashed, n)
        if duplicates:
            raise ValueError("；".join(duplicates))
        return self


# ───────────── 文章 ─────────────

class Component(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    type: str


class _ArticleShape(BaseModel):
    """原文與譯文共有的結構。兩者的 title、tags 規則不同，所以各自定義，不互相繼承。"""
    model_config = ConfigDict(extra="allow")
    components: list[Component]


class Article(_ArticleShape):
    """文章檔（與 tools/hk01_web.py 的輸出同一結構）。其餘欄位（meta、mainChannel）保留但不送給模型。"""
    title: str
    tags: list[str] = []


class Translation(_ArticleShape):
    """譯文：title 與 tags 可以缺少，缺少就是漏段，由 check 判定，不在這裡擋。"""
    title: str | None = None
    tags: list[str] | None = None


# ───────────── Run 紀錄 ─────────────

class Attempt(StrictModel):
    """一次請求嘗試。429、5xx、timeout 會重試，每次嘗試都留一筆。"""
    http_status: int | None
    outcome: Outcome
    reason: str | None = None
    latency_seconds: float


class RunKey(StrictModel):
    article_id: Slug
    approach: Slug
    direction: Direction
    input_variant: InputVariant
    run_index: int = Field(ge=1)

    @classmethod
    def of(cls, record: "RunKey") -> "RunKey":
        """從 Run 紀錄取出 key（紀錄本身也是 RunKey 的子類，直接比較不會相等）。"""
        return cls(**{name: getattr(record, name) for name in cls.model_fields})

    @property
    def label(self) -> str:
        """終端輸出與錯誤訊息裡指稱一個 Run 的寫法：只有 id 與類型。"""
        return f"{self.article_id} {self.approach} {self.direction} {self.input_variant} r{self.run_index}"


class RunRecord(RunKey):
    outcome: Outcome
    reason: str | None = None   # Block 的原因，或 error／malformed 的說明
    attempts: list[Attempt]
    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    latency_seconds: float | None = None   # 最後一次嘗試的耗時
    concurrency: int | None = None   # baseline 的 Run 不經 API，下面幾項也都沒有
    provider: str | None = None
    model_version: str | None = None   # 實際回應的模型版本
    prompt_id: str | None = None
    prompt_hash: str | None = None
    term_list_hash: str | None = None   # 模型看到的 Term List（原文詞與譯法）的 hash；沒有設 Term List 時為 None
    config_hash: str | None = None
    baseline_label: str | None = None   # baseline 的 Run：來源的 meta.baseline 標籤（服務與 opencc 的版本）
    raw_response: str | None = None   # 相對於輸出目錄
    translation: str | None = None
    created_at: str

    @property
    def is_baseline(self) -> bool:
        return self.baseline_label is not None

    @model_validator(mode="after")
    def _llm_runs_have_their_request_details(self):
        # 這幾項只有 baseline 的 Run 可以沒有；否則格式壞掉的 Run 會被當成 baseline，在報告裡被靜默略過
        if not self.is_baseline:
            missing = [name for name in ("provider", "prompt_id", "prompt_hash", "config_hash", "concurrency") if getattr(self, name) is None]
            if missing:
                raise ValueError("不是 baseline 的 Run 缺少：" + "、".join(missing))
        return self


# ───────────── Component 結果 ─────────────

# Deterministic check 的 Finding 子類
OMISSION = "omission"
EXTRA_COMPONENT = "extra-component"
DUPLICATE_COMPONENT = "duplicate-component"
TYPE_CHANGED = "type-changed"
TERM_MISMATCH = "term-mismatch"
RESIDUAL_HAN = "residual-han"
RESIDUAL_TRADITIONAL = "residual-traditional"


class Finding(StrictModel):
    """評核者指出的一個具體問題（計分）。Warning 不計分，不用這個 model。"""
    source: Literal["deterministic"]
    dimension: Dimension
    subtype: str
    severity: Severity
    source_span: str = ""
    target_span: str = ""
    reason: str


class WarningKind(StrEnum):
    LENGTH_RATIO = "length-ratio"
    NUMBER_MISSING = "number-missing"
    QUOTE_COUNT = "quote-count"
    SUSPECTED_REFUSAL = "suspected-refusal"
    UNCHANGED_TRANSLATABLE = "unchanged-translatable"
    UNTRANSLATABLE_CHANGED = "untranslatable-changed"


class CheckWarning(StrictModel):
    """Deterministic check 標出、交人手看但不計分的提示。程式判斷不夠可靠的情況只產生 Warning，不產生 Finding。"""
    kind: WarningKind
    detail: str = ""


class CheckMeta(StrictModel):
    """check 當時用的規則版本與外部輸入。report 用它判斷 check 的結果是不是已經過期。"""
    rules_version: str
    term_list_hash: str | None   # check 用到的 Term List 內容（備註除外）的 hash
    length_ratio: dict[Direction, LengthRatioRange]

    def differing_fields(self, other: "CheckMeta") -> list[str]:
        return [name for name in type(self).model_fields if getattr(self, name) != getattr(other, name)]


class ComponentResult(RunKey):
    """一個 Component × Approach × Direction × Run 的檢查結果。source_type 為 None 表示原文沒有這個 id（譯文多出來的）。"""
    component_id: str
    source_type: str | None
    findings: list[Finding] = []
    warnings: list[CheckWarning] = []

    @property
    def n_omissions(self) -> int:
        return sum(1 for f in self.findings if f.subtype == OMISSION)
