# 07: 阿里雲 DashScope 與方案 B

**What to build:** 方案 B（阿里雲 Qwen）可以和其他 Approach 用同一條流程執行，只會送到 International（新加坡）endpoint，平台內容審查的錯誤記為 Block。程式現在就可以做完並以假 transport 測試；真正執行要等 whitelist 批出，以及 Pan 提供 `DASHSCOPE_API_KEY`。詳見 spec 第 3 節。

**Blocked by:** 01

**Status:** ready-for-human

- [x] 只接受新加坡 endpoint；設定中寫其他 endpoint 時，指令被拒絕
- [x] 內容審查錯誤（例如 `DataInspectionFailed`）→ `block`，並附原因；解讀規則以官方錯誤碼文件為準，並寫成回應樣本
- [x] 只有在 Approach 設定明確開啟時才送 `X-DashScope-DataInspection` header
- [x] key 只從 `DASHSCOPE_API_KEY` 讀取
- [x] 報告中方案 B 的 Block 率附原因分佈，讓人分辨平台審查和其他錯誤
- [ ] whitelist 批出前是否先跑一小批，由 Pan 決定

## Comments

### 2026-11-09 程式完成，只剩 Pan 的決定

除了最後一項（Pan 決定要不要在 whitelist 前先跑一小批），都完成。解讀規則對照官方的錯誤碼文件與 OpenAI 相容 API 文件寫成，樣本在 `tests/dashscope_samples.py`。要請 Pan 知道的事：

- **`X-DashScope-DataInspection` 與 whitelist 無關（spec 的假設可能是誤解）**：官方文件說這個 header 是啟用「AI 安全護欄」這個額外服務（值是 JSON 字串，例如 `{"input":"cip","output":"cip"}`），**該服務僅北京區可用**；它是在模型內建審查之外多加一層，不是用來關掉審查。文件裡真正的「加白」是另一條流程：向阿里雲客戶經理提出內容加白申請，把內容加入豁免名單，與這個 header 無關。所以在新加坡送這個 header 多半沒用。程式照 ticket 做成「Approach 設定的 `data_inspection` 有寫才送，值照原樣」，沒有替你猜值；請 Pan 向阿里雲確認 whitelist 的實際做法。
- **endpoint**：新加坡有兩種網域，都接受：舊的 `dashscope-intl.aliyuncs.com`（預設，官方說仍然可用），以及各 workspace 自己的 `<workspace>.ap-southeast-1.maas.aliyuncs.com`（官方建議遷移到這種）。其他一律拒絕。
- **官方文件沒寫的地方，我做了選擇**：(1) 錯誤 body 的格式文件沒寫，所以 `error.code`、`error.type` 和頂層 `code` 都認；(2) `finish_reason: content_filter` 官方文件沒列，但意思就是被過濾，記為 Block；(3) `message.refusal` 有內容記為 Refusal（官方文件說 Qwen 的這個欄位永遠是 null，這只是防範；`content_filter` 與 refusal 的測試樣本不是真實的 DashScope 回應）；(4) 除了 `DataInspectionFailed`，`FaqRuleBlocked`、`CustomRoleBlocked`、`IPInfringementSuspect` 也是平台因為內容拒絕處理，一併記為 Block。
- **Block 不重試**，即使平台回的是 429 或 5xx 但錯誤碼是內容審查；429 的 `Throttling` 與 5xx 才退避重試。
- 模型 id 沒有訂：文件頁列的是 `qwen-plus`、`qwen-max` 之類的通用模型，沒有翻譯專用的；請 Pan 選。
- 輸出長度上限用 `max_tokens`（每個 Qwen 模型都支援）；`max_completion_tokens` 官方建議新整合用，但只有較新的模型有文件。
- 沒有用真 key 跑過；真跑要等 whitelist 與 `DASHSCOPE_API_KEY`。

### 2026-11-09 review 之後的修正

- **endpoint 驗證改成對原始字串整個比對**：原本先解析再驗證，但送出去的是原始字串；結尾的 `?`、`#`、換行、tab、空的帳號、`:0` 埠號、大寫的 scheme 與 host 會通過驗證，原樣送出就變成另一個網址（host 沒有跑掉，但這是守著「敏感文章不能送去別處」的檢查，不該有這種落差）。
- **Block 不會因為回應的其他部分怪怪的而少算**：`finish_reason: content_filter` 的回應即使 `message` 是空的、`usage` 型別不對，仍然記為 Block；Gemini 的 Block 也一樣（`promptFeedback` 的 Block 與安全類的 `finishReason`，即使 `usageMetadata` 或 `modelVersion` 怪怪的）。只有 ok 的 Run 才要求 token 數和模型版本正確。
- **內容審查的錯誤碼不論 HTTP 狀態碼是什麼都是 Block**（包括 429、5xx，以及 200），也不重試；報告的原因分佈統一寫成官方的寫法（`DataInspectionFailed` 與 `data_inspection_failed` 不再算成兩種）。
- `message.refusal` 不是字串時記為 error，不會被忽略而變成 ok。
- 括號層數多到 json 解析器超出遞迴上限的回應或模型輸出，原本會讓指令當掉（Gemini 也有同樣的問題）；現在記為 error（回應）或 malformed（模型輸出）。
