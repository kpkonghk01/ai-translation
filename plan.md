# AI 翻譯測試計畫（繁→英、繁→簡）

Status: draft
Owner: Pan
Last updated: 2026-10-16

## 背景

- Ivan 提議由 Pan 先 study translation with AI，範圍：
  - 繁 → 英：之前傾過，未啟動
  - 繁 → 簡：目標是取代現時的「逐字式 translation service」
- 已知問題：部份文章會中 online model 的 content filter。portal 已遇過 Gemini `PROHIBITED_CONTENT`（PR #59）。fallback 到其他 provider 也未必 cover 得晒。
- 規模假設：每日約 400 篇，每篇 ≤ 5k 中文字，兩個方向共 800 個 job。

## 已決定

- 阿里雲會申請 content filter whitelist（2026-10-16）
- 測試集加入約 10 篇一般文章做對照
- 每篇預先標記敏感句；方案 A 每篇跑 3 次；提早申請 AWS GPU quota

## 要回答的問題

1. 哪個方案可以穩定翻譯政治敏感 / 色情內容，而且**不被 block、不拒絕、不淡化**？
2. 翻譯質素是否可用（以 Gemini 3.5 Flash-Lite 為 baseline 比較）？
3. 如果用 self-host，throughput 和成本是否可行？

## 測試方案

| # | 方案 | 類型 | 備註 |
|---|---|---|---|
| A | Gemini 3.5 Flash-Lite（`gemini-3.5-flash-lite`；2026-11-09 由 3.1 改） | API（baseline） | safety settings 全部調到最低，只剩不可關的 block（如 `PROHIBITED_CONTENT`）。portal 已接好 |
| B | 阿里雲 Model Studio（Qwen-MT / Qwen3.6），關 content filter | API | 會申請 whitelist。批出後在 header 加 `X-DashScope-DataInspection: {"input":"disable","output":"disable"}`；模型本身仍可能拒絕。見「待確認」 |
| C | Self-host dealigned model，40B 以下 MoE | Self-host | 例如 Qwen3.6-35B-A3B、Gemma4-26B-A4B 的 abliterated 版本 |
| D | Self-host，20B 以下 | Self-host | 例如 Hy-MT2-7B、TranslateGemma-12B（有 dealigned 版本就用） |
| E | OpenCC `hk2s` | 非 AI，只做繁→簡 | 對照現時的逐字式做法（待確認現時 service 是否 OpenCC） |
| F（可選） | Hy-MT2-30B-A3B 經 OpenRouter | API | 很便宜（約 US$2 / 1,000 篇），可以順便對照翻譯專用模型。2026-11-11 實測：Tencent 對約 2–3 成的請求回空內容（見 ticket 06），Run 大多拿不到完整譯文 |
| F7（可選） | Hy-MT2-7B 經 OpenRouter（`tencent/hy-mt2-7b`） | API | 同系列的 7B：同樣的 Tencent、同樣的請求，pilot 沒有空內容，所以加進第一批當 Hy-MT2 的資料點（Pan 於 2026-11-11 同意試；總數 5 個模型以內）。1.8B 試過，繁→簡幾乎沒轉成簡體，不用 |
| G（可選） | Qwen3.6-35B-A3B 經 OpenRouter（`qwen/qwen3.6-35b-a3b`） | API | 阿里雲暫時沒有 key，先用它看 Qwen 模型本身的行為；不經阿里雲平台的內容審查，與方案 B 分開報告（Pan 於 2026-11-09 授權自選額外模型；總數 5 個模型以內） |

C、D 在 Phase 1–2 只測質素，可以先在本機（llama.cpp / MLX，RAM 夠的話）或短時間開的 EC2 跑。到 Phase 3 才正式在 EC2 上測效能。

## 測試集

**主力：約 30 篇政治敏感或色情文章**

- 篩選方法：候選文章先過方案 A，**優先選會被 block 的**，並記錄 block reason。
- 來源：
  1. portal DB 裡的 failed turns：`Session.history` 中 `error.code = "CONTENT_BLOCKED"` 的 turn，再用 `resourceId` 對回 CMS 文章（resourceId 是否等於文章 id 待確認）。PR #59 之後才開始有記錄。
  2. 編輯部 / Ivan 推薦。
  3. 在 CMS 用關鍵字搜尋。
- 建議分佈：政治約 15 篇（例如六四、國安法案件、社運），色情 / 性罪行約 15 篇。
- 長度要覆蓋短（< 1k 字）、中（1–3k 字）、長（3–5k 字）。長文章可以順便測 Hy-MT2 的 8k context 分段。
- 每篇預先標記 2–3 句最敏感的句子，Phase 1 用來檢查有沒有被淡化。

**對照：約 10 篇一般文章**，用來測一般質素，並確認 dealign 不會影響正常翻譯。

**Glossary：約 50 條**，包括官員英文名、地名、機構名，用來量度專有名詞的準確度。

測試集和輸出都含敏感內容，**不要放進 repo**，要放在有權限控制的地方。

## 測試流程

### Phase 0：準備

- [ ] 向 alexyau 申領帳號：
  - 阿里雲 Model Studio，並經 account manager 申請 content filter whitelist
  - AWS，並申請東京區的 GPU quota（Running On-Demand G and VT instances）
  - Gemini API key
  - OpenRouter（可選）
- [ ] 確認現時逐字式 service 是否 OpenCC，以及用哪個 config。
- [ ] 準備測試集、glossary、統一 prompt（只輸出譯文、保留段落、注入 glossary）。
- [ ] 寫一個 runner：文章 × 方案 × 方向，每次輸出連同 metadata（blocked、block reason、tokens、latency）一起存檔。

### Phase 1：Block / 拒絕測試（主要）

30 篇 × 2 個方向 × 每個方案。方案 A 每篇跑 3 次，因為 block 不一定每次都一樣。每次記錄：

| 指標 | 判斷方法 |
|---|---|
| Block | API 回傳 block 或 error |
| 拒絕 | 模型回覆「無法翻譯」之類的內容 |
| 漏譯 | 段落數對不上，或譯文長度比例異常 |
| 淡化 / 改寫 | 人手核對預先標記的敏感句 |

### Phase 2：質素

- **人手評分**（1–5 分）：準確、完整、流暢、專有名詞。繁→英需要懂英文的 reviewer，由誰負責待定。
- **自動檢查**：
  - 段落數、長度比例
  - glossary 命中率
  - 繁→簡的輸出有沒有殘留繁體字（用 OpenCC 反向檢查）
  - 繁→英的輸出有沒有殘留中文字
- **LLM judge（可選）**：judge 本身也可能被 filter block，要用沒有 filter 的模型。

### Phase 3：Self-host 效能（只測 Phase 1–2 合格的 self-host 方案）

- **機型**（新加坡沒有 g6 / g6e / g7e，要用東京）：
  - 20B 以下：東京 g6.xlarge（L4 24GB），每小時 $1.167
  - 35B MoE 4-bit：東京 g6.2xlarge（L4 24GB，32GiB RAM），每小時 $1.418
- **Serving**：用 vLLM。dealigned 版本要找 vLLM 支援的量化格式（AWQ / GPTQ / FP8）；只有 GGUF 的話，並行 throughput 會大跌。
- **量度**：
  - 單線程 tps
  - 4 / 8 個並行時的整體 tps
  - 首 token 延遲
  - 從 EBS 載入模型的時間（gp3 預設 125 MB/s 對比調高到約 600 MB/s）
  - VRAM 用量
- **Replay 一日的量**：800 個 job，用真實文章的長度分佈，看能否在 16h 內完成，以及開機後消化前一晚 backlog 要多久。

## 成功準則（建議，待 Stephen / Ivan 確認）

- 測試集 0 篇被 block 或拒絕
- 0 篇漏段
- 敏感句 100% 忠實翻譯
- 人手平均分 ≥ baseline（只比較方案 A 沒有被 block 的文章）
- Self-host：最差情況（全部 5k 字）可以在 16h 內完成 800 個 job

## 成本參考（2026-10-16 估算，每月）

| 方案 | 最差（全部 5k 字） | 一般（假設平均 2k 字） |
|---|---|---|
| Self-host 東京 g6.2xlarge，每日 16h（35B MoE） | 約 US$700–720 | 同左（固定成本） |
| Self-host 東京 g6.xlarge，每日 16h（20B 以下） | 約 US$580 | 同左（固定成本） |
| Gemini 3.1 Flash-Lite（估算時的型號；現改用 3.5） | 約 US$216 | 約 US$90 |
| Hy-MT2-30B-A3B 經 OpenRouter | 約 US$46 | 約 US$20 |

Self-host 比 API 貴 3–7 倍。所以 self-host 的理由是避開 filter / 用 uncensored 模型，不是成本。

Tps 估算（按 GPU 記憶體頻寬推算，未實測，Phase 3 會驗證）：

- 35B-A3B MoE 4-bit 在 L4 上，單線程約 50–70 tps
- 7B dense 在 L4 上：FP8 約 30–40 tps，4-bit 約 50–60 tps
- 最差情況要平均約 69 tps 才能在 16h 內做完，所以**一定要開並行**

## 待確認 / 風險

- **阿里雲 whitelist**（已決定申請）：
  - 不送 header 只是跳過額外的檢查，基礎審查仍然會做，所以一定要 whitelist。
  - 批出要多久？whitelist 實際關到哪一層？
  - 即使關了，模型本身仍可能拒絕，Phase 1 會量度。
- **Dealigned 模型**：
  - abliteration 只是去除拒絕；Qwen 系的淡化寫法來自訓練資料，可能仍然存在。
  - 社群版本的來源和授權要先確認。
- **Hy-MT2**：
  - 1.5 版的 license 排除 EU / UK / 南韓，Hy-MT2 要看清楚。
  - 只有 8k context，長文章要分段送。
- **上架時間**：翻譯版是否要即時上架？會影響 self-host 每日 16h 的開機窗口。
- **規模數字**：平均文章長度、每日篇數（現時假設 400 篇）都要確認。
- **測試集權限**：含色情內容，存放位置和存取權限要先定好。
- **AWS quota**：GPU quota 申請可能需時，要提早申請。

## 參考資料

- [HardMTBench: Chinese-English translation stress test](https://arxiv.org/html/2605.28315)
- [Hy-MT2 GitHub](https://github.com/Tencent-Hunyuan/Hy-MT2)、[Hy-MT2 technical report](https://arxiv.org/html/2605.22064v1)
- [TranslateGemma Technical Report](https://arxiv.org/pdf/2601.09012)
- [bilibili Index-Translate evaluation](https://github.com/bilibili/Index-Translate/blob/main/docs/evaluation.md)
- [Alibaba Cloud Model Studio: DashScope API reference](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-dashscope)、[Error codes](https://www.alibabacloud.com/help/en/model-studio/error-code)、[bailian-content-inspection](https://github.com/eXtremeProgramming-cn/bailian-content-inspection)
- [Amazon EC2 G7e regions (2026-07)](https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-g7e-additional-regions/)
- AWS 公開 EC2 on-demand 價目表（2026-10-15 版）
