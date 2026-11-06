"""所有檔案格式的 pydantic model：設定、文章、Run 紀錄。其他模組引用這裡，不重抄欄位或 enum。

設定與紀錄都禁止多出來的欄位（拼錯欄位名會令驗證失敗，而不是被靜默忽略）。
"""
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# 會用在檔名和網址路徑裡的 id：只允許安全字元，避免設定檔把輸出寫到別的目錄
Slug = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=64)]
ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=100)]


class Direction(StrEnum):
    TRAD_TO_EN = "trad-to-en"   # 繁→簡隨 ticket 05 加入


class InputVersion(StrEnum):
    FULL = "full"   # main-only 隨 ticket 02 加入


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


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ───────────── 設定 ─────────────

PROVIDER_KEY_ENV = {"gemini": "GEMINI_API_KEY"}


class RetryConfig(Strict):
    max_attempts: int = Field(default=4, ge=1)
    base_delay_seconds: float = Field(default=2.0, ge=0)


class ApproachConfig(Strict):
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
    def _check(self):
        # key 只能送去它自己的 provider，不能把別家的 key 指給這個 endpoint
        if self.api_key_env != PROVIDER_KEY_ENV[self.provider]:
            raise ValueError(f"provider {self.provider} 的 api_key_env 必須是 {PROVIDER_KEY_ENV[self.provider]}")
        if self.concurrency != 1:
            raise ValueError("目前只支援循序執行（concurrency 必須是 1）")
        return self


class ArticleSet(Strict):
    dir: str
    ids: list[Slug] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self):
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("ids 有重覆的文章 id")
        return self


class ApproachEntry(Strict):
    config: str
    runs: int = Field(ge=1)


class ExperimentConfig(Strict):
    id: Slug
    articles: ArticleSet
    directions: list[Direction] = Field(min_length=1)
    approaches: list[ApproachEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self):
        if len(set(self.directions)) != len(self.directions):
            raise ValueError("directions 有重覆")
        return self


# ───────────── 文章 ─────────────

class Component(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    type: str


class Article(BaseModel):
    """文章檔（與 tools/hk01_web.py 的輸出同一結構）。其餘欄位（meta、mainChannel）保留但不送給模型。"""
    model_config = ConfigDict(extra="allow")
    title: str
    tags: list[str] = []
    components: list[Component]


class Translation(Article):
    """譯文：title 與 tags 可以缺少，缺少就是漏段，由 check 判定，不在這裡擋。"""
    title: str | None = None
    tags: list[str] | None = None


# ───────────── Run 紀錄 ─────────────

class Attempt(Strict):
    """一次請求嘗試。429、5xx、timeout 會重試，每次嘗試都留一筆。"""
    http_status: int | None
    outcome: Outcome
    reason: str | None = None
    latency_seconds: float


class RunKey(Strict):
    article_id: Slug
    approach: Slug
    direction: Direction
    input_version: InputVersion
    run_index: int = Field(ge=1)

    @classmethod
    def of(cls, record: "RunKey") -> "RunKey":
        """從 Run 紀錄取出 key（紀錄本身也是 RunKey 的子類，直接比較不會相等）。"""
        return cls(**{name: getattr(record, name) for name in cls.model_fields})


class RunRecord(RunKey):
    outcome: Outcome
    reason: str | None = None   # Block 的原因，或 error／malformed 的說明
    attempts: list[Attempt]
    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    latency_seconds: float | None = None   # 最後一次嘗試的耗時
    concurrency: int
    provider: str
    model_version: str | None = None   # 實際回應的模型版本
    prompt_id: str
    prompt_hash: str
    config_hash: str
    raw_response: str | None = None   # 相對於輸出目錄
    translation: str | None = None
    created_at: str


# ───────────── Component 結果 ─────────────

class Finding(Strict):
    """評核者指出的一個具體問題（計分）。Warning 不計分，不用這個 model。"""
    source: Literal["deterministic"]
    dimension: Dimension
    subtype: str
    severity: Severity
    source_span: str = ""
    target_span: str = ""
    reason: str


class ComponentResult(RunKey):
    """一個 Component × Approach × Direction × Run 的檢查結果。source_type 為 None 表示原文沒有這個 id（譯文多出來的）。"""
    component_id: str
    source_type: str | None
    findings: list[Finding] = []
