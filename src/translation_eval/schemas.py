"""所有檔案格式的 pydantic model：設定、文章、Run 紀錄。其他模組引用這裡，不重抄欄位或 enum。

設定與紀錄都禁止多出來的欄位（拼錯欄位名會令驗證失敗，而不是被靜默忽略）。
"""
import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .textrules import squash

# 會用在檔名和網址路徑裡的 id：只允許安全字元，避免設定檔把輸出寫到別的目錄
Slug = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=64)]
# 模型名：OpenRouter 的是「作者/名稱」（名稱後面可能有 :free 這類後綴），其他 provider 的不能有 / 和 :，
# 因為 Gemini 把模型名放進網址路徑；這個差別由 ApproachConfig 按 provider 檢查
ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$", max_length=100)]
# OpenRouter 的 provider slug（請求的 only 用）：小寫，可以帶 /variant 或 /region
ProviderSlug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*$", max_length=60)]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# HTTP header 的值：可以印出的 ASCII，沒有換行，才不會被拿去夾帶別的 header
HeaderValue = Annotated[str, StringConstraints(pattern=r"^[\x20-\x7e]+$", max_length=500)]


class Direction(StrEnum):
    TRAD_TO_EN = "trad-to-en"
    TRAD_TO_SIMPLIFIED = "trad-to-simplified"




class RequestMode(StrEnum):
    ARTICLE_JSON = "article-json"   # 整篇 JSON 進、同結構 JSON 出（可以按 max_input_chars 切塊）
    PER_COMPONENT = "per-component"   # 每個要翻譯的字串一個請求，純文字進出


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


class Domain(StrEnum):
    GENERAL = "general"
    CHINA_POLITICAL = "china-political"   # 這類文章的所有事實都算 political fact


class FactKind(StrEnum):
    ACTOR = "actor"
    VICTIM = "victim"
    ACT = "act"
    DETAIL = "detail"
    QUANTITY = "quantity"
    LEGAL_STATUS = "legal_status"
    ATTRIBUTION = "attribution"
    QUOTE = "quote"
    TERM = "term"


class Verdict(StrEnum):
    PRESENT = "Present"
    WEAKENED = "Weakened"
    ABSENT = "Absent"


class WeakeningType(StrEnum):
    A = "a"   # 去掉行為者
    B = "b"   # 委婉或中性化的用詞
    C = "c"   # 數量變模糊
    D = "d"   # 具體細節被拿掉或泛化
    E = "e"   # 對原文斷言的事加上限定
    F = "f"   # 直接引語裡的粗話或狠話被洗乾淨
    AMPLIFICATION = "amplification"   # 比原文更聳動；不計入淡化率


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ───────────── 設定 ─────────────

PROVIDER_KEY_ENV = {"gemini": "GEMINI_API_KEY", "dashscope": "DASHSCOPE_API_KEY", "openrouter": "OPENROUTER_API_KEY"}

# 阿里雲只用 International（新加坡）：舊的共用網域，或各 workspace 自己的 ap-southeast-1 網域。大陸版的條款寫明
# 紀錄會保留並報告當局，政治敏感文章不能送，所以其他 endpoint 一律不接受。對原始字串整個比對，不先解析：
# 解析會吃掉換行、空的 ? 和 #，驗證通過的網址原樣送出就變成另一個網址
DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
_DASHSCOPE_BASE_URL = re.compile(
    r"https://(?:dashscope-intl\.aliyuncs\.com|[a-z0-9-]+\.ap-southeast-1\.maas\.aliyuncs\.com)/compatible-mode/v1/?")


class RetryConfig(StrictModel):
    max_attempts: int = Field(default=4, ge=1)
    base_delay_seconds: float = Field(default=2.0, ge=0)


class OpenRouterRouting(StrictModel):
    """OpenRouter 的 provider 允許清單：只有 Pan 看過 providers 指令列出的清單、確認過（排除位於中國大陸的 provider），
    才可以寫進來。清單是空的或沒有確認日期，adapter 就拒絕組出請求。"""
    only: list[ProviderSlug] = []
    confirmed_on: date | None = None

    @model_validator(mode="after")
    def _unique_only(self):
        if len(set(self.only)) != len(self.only):
            raise ValueError("only 有重覆的 provider")
        return self


class ApproachConfig(StrictModel):
    id: Slug
    family: str = Field(min_length=1)
    provider: Literal["gemini", "dashscope", "openrouter"]
    model: ModelName
    api_key_env: str
    base_url: str | None = None   # 只有 dashscope 用；不設就用 DASHSCOPE_DEFAULT_BASE_URL
    data_inspection: HeaderValue | None = None   # 只有 dashscope 用：X-DashScope-DataInspection 的值；不設就不送這個 header
    routing: OpenRouterRouting | None = None   # 只有 openrouter 用
    request_mode: RequestMode = RequestMode.ARTICLE_JSON
    max_input_chars: int | None = Field(default=None, ge=1)   # 只用於 article-json：超過就按 Component 順序切塊，每塊一個請求
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
        if self.provider == "dashscope":
            if not _DASHSCOPE_BASE_URL.fullmatch(self.base_url or DASHSCOPE_DEFAULT_BASE_URL):
                raise ValueError("base_url 只能是阿里雲 International（新加坡）的網址：https://dashscope-intl.aliyuncs.com/compatible-mode/v1，"
                                 "或 https://<workspace>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1（全小寫，不能有空白、帳號、埠號、? 或 #）")
        elif self.base_url is not None or self.data_inspection is not None:
            raise ValueError("base_url 和 data_inspection 只有 dashscope 用")
        if self.request_mode is RequestMode.PER_COMPONENT and self.max_input_chars is not None:
            raise ValueError("max_input_chars 只用於 article-json（切塊）；per-component 每個請求本來就只有一段文字")
        if self.provider != "openrouter" and self.routing is not None:
            raise ValueError("routing 只有 openrouter 用")
        if self.provider == "openrouter":
            if self.model.count("/") != 1:
                raise ValueError("openrouter 的 model 要寫成「作者/名稱」")
        elif "/" in self.model or ":" in self.model:
            raise ValueError("model 不能有 / 或 :")
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


class JudgeRole(StrEnum):
    PRIMARY = "primary"   # 決定 Gate
    EXPERIMENT = "experiment"   # 只作實驗，結果單獨報告


class JudgeConfig(ApproachConfig):
    """Judge 設定：與 Approach 設定相同，另加角色。temperature 固定 0；輸出是 JSON，所以只用 article-json、不切塊。"""
    role: JudgeRole
    temperature: float | None = Field(default=0.0, ge=0, le=2)

    @model_validator(mode="after")
    def _judge_rules(self):
        if self.temperature != 0:
            raise ValueError("Judge 的 temperature 固定是 0")
        if self.request_mode is not RequestMode.ARTICLE_JSON or self.max_input_chars is not None:
            raise ValueError("Judge 只用 article-json 而且不切塊：它的輸出是 JSON")
        return self


class SameFamilyException(StrictModel):
    """Judge 與被評的 Approach 同家族時，要針對這個組合寫明理由才可以執行；所有例外都會列在報告開頭。"""
    approach: Slug
    judge: Slug
    reason: NonBlank


class JudgeEntry(StrictModel):
    config: str


class JudgingConfig(StrictModel):
    judges: list[JudgeEntry] = Field(min_length=1)
    same_family_exceptions: list[SameFamilyException] = []
    mqm_run_limit: dict[Slug, Annotated[int, Field(ge=1)]] = {}   # Approach id → 只有 Run 序號不超過這個數的 Run 做 MQM
    primary_judges: dict[Slug, Slug] = {}   # domain 分組（或 ground_truth.group_overrides 的分組）→ 決定 Gate 的主 Judge 的 id


class Weights(StrictModel):
    """每個 Finding 的罰分：按 Severity。改了只要重算報告，不用重跑 Judge。"""
    minor: float = Field(default=1, ge=0)
    major: float = Field(default=5, ge=0)
    critical: float = Field(default=25, ge=0)


class GroundTruthStatus(StrEnum):
    DRAFT = "draft"   # 還沒有人手確認
    APPROVED = "approved"


class GroundTruthRef(StrictModel):
    """ground truth 檔（現有格式，可以多個）的位置與狀態。group_overrides：文章 id → 分組名，把文章從它的 domain 分組
    另列成一組（兩篇文章的歸屬未決，由 Pan 在設定裡決定，不用改程式）。"""
    paths: list[str] = Field(min_length=1)
    status: GroundTruthStatus
    group_overrides: dict[Slug, Slug] = {}


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
    judging: JudgingConfig | None = None
    ground_truth: GroundTruthRef | None = None
    weights: Weights = Weights()
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
    unit: str | None = None   # 一個 Run 有好幾個請求（切塊、逐 Component）時，這次嘗試屬於哪一個
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
    served_provider: str | None = None   # OpenRouter：回應寫的、實際服務這個請求的 provider（有好幾個就都列出）
    worst_unit: str | None = None   # 一個 Run 有好幾個請求，而且結果不是 ok：結果最差的是哪一個
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


# ───────────── Judge ─────────────

class JudgeFinding(StrictModel):
    """Judge 指出的一個問題，已通過逐字驗證。"""
    dimension: Dimension
    subtype: NonBlank
    severity: Severity
    source_span: str   # 原文的逐字片段；可以是空的（例如漏譯時看不到對應的原文片段時），但兩個片段不能同時是空的
    target_span: str
    reason: str


class SourceDefect(StrictModel):
    source_span: NonBlank
    issue: NonBlank


class InvalidItem(StrictModel):
    """Judge 的輸出裡不合格的一條：片段不是逐字子字串、Dimension 或 Severity 不在清單內、結構不符。原文保留供審閱。"""
    kind: Literal["finding", "source_defect", "verdict"]
    why: str
    raw: str   # 模型輸出的那一條，存成 JSON 文字（有長度上限）。不存成巢狀結構：太深的結構會讓紀錄寫不進去或讀不回來
    fact: str | None = None   # kind 是 verdict 時：作廢的是哪一條事實（事實 key 的最後一段）；模型回了不認識的 id 時沒有


class FactVerdict(StrictModel):
    """Judge 對一條 Must-retain fact 的 verdict，已通過驗證（引用是譯文的逐字子字串）。"""
    fact: str   # 事實 key 的最後一段：事實文字的 SHA-256（前兩段是這筆紀錄的文章 id 與 Component id）
    verdict: Verdict
    weakening_type: WeakeningType | None = None
    quote: str = ""   # Present 與 Weakened 必須有；Absent 沒有
    reason: str = ""


class JudgeCall(RunKey):
    """Judge 對一個 Run 的一個 Component 的一次請求（每個 Component 要評兩次，call_index 是 1 或 2）。
    task 是 mqm（Finding）或 fact（Must-retain fact 的 verdict）。"""
    component_id: str
    judge: Slug
    task: Literal["mqm", "fact"] = "mqm"
    call_index: int = Field(ge=1, le=2)
    outcome: Outcome
    reason: str | None = None
    attempts: list[Attempt]
    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    model_version: str | None = None
    served_provider: str | None = None
    prompt_hash: str
    config_hash: str
    findings: list[JudgeFinding] = []
    source_defects: list[SourceDefect] = []
    verdicts: list[FactVerdict] = []
    invalid: list[InvalidItem] = []
    input_hash: str | None = None   # fact 任務：送出的事實與 anchor 的 hash；ground truth 改了，這筆紀錄就過期
    raw_response: str | None = None
    created_at: str


# ───────────── Ground truth（現有格式，見 tools/render_ground_truth.py） ─────────────

class GtFact(StrictModel):
    kind: FactKind
    fact: NonBlank


class GtItem(StrictModel):
    """一個 Sensitive sentence：它所在的 Component、定位用的 anchor，和譯文必須保留的事實。"""
    component_id: NonBlank
    anchor: NonBlank
    why: str = ""
    must_retain: list[GtFact] = Field(min_length=1)


class GtSourceDefect(StrictModel):
    component_id: NonBlank
    anchor: NonBlank
    issue: str = ""


class GtArticle(StrictModel):
    """文章用 article_id（沒有的話用 session_id）指向來源。domain 沒寫就是 general。"""
    article_id: str | None = None
    session_id: str | None = None
    role: Literal["sensitive", "control"]
    domain: Domain = Domain.GENERAL
    note: str = ""
    items: list[GtItem]
    source_defects: list[GtSourceDefect] = []

    @model_validator(mode="after")
    def _has_an_id(self):
        if not (self.article_id or self.session_id):
            raise ValueError("要有 article_id 或 session_id")
        return self

    @property
    def id(self) -> str:
        return self.article_id or self.session_id


class GtFile(StrictModel):
    status: str | None = None   # 檔案自己寫的備註（例如「draft，待人手確認」）；狀態以實驗設定的 ground_truth.status 為準
    articles: list[GtArticle]


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
