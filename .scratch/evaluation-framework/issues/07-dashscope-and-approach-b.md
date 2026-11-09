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

- **`X-DashScope-DataInspection` 的值我沒有替你猜**：官方文件說它是在內建審查之外多加一層 AI 護欄（值是 JSON 字串，例如 `{"input":"cip","output":"cip"}`），沒有寫任何值能關掉審查；這和 spec 假設的「whitelist 批出之後才送」不完全一致。所以程式不寫死它的值：Approach 設定的 `data_inspection` 有寫才送，值照原樣。whitelist 批出後，請向阿里雲確認要送什麼。
- **endpoint**：新加坡有兩種網域，都接受：舊的 `dashscope-intl.aliyuncs.com`（預設，官方說仍然可用），以及各 workspace 自己的 `<workspace>.ap-southeast-1.maas.aliyuncs.com`（官方建議遷移到這種）。其他一律拒絕。
- **官方文件沒寫的地方，我做了選擇**：(1) 錯誤 body 的格式文件沒寫，所以 `error.code`、`error.type` 和頂層 `code` 都認；(2) `finish_reason: content_filter` 官方文件沒列，但意思就是被過濾，記為 Block；(3) `message.refusal` 有內容記為 Refusal；(4) 除了 `DataInspectionFailed`，`FaqRuleBlocked`、`CustomRoleBlocked`、`IPInfringementSuspect` 也是平台因為內容拒絕處理，一併記為 Block。
- **Block 不重試**，即使平台回的是 429 或 5xx 但錯誤碼是內容審查；429 的 `Throttling` 與 5xx 才退避重試。
- 模型 id 沒有訂：文件頁列的是 `qwen-plus`、`qwen-max` 之類的通用模型，沒有翻譯專用的；請 Pan 選。
- 沒有用真 key 跑過；真跑要等 whitelist 與 `DASHSCOPE_API_KEY`。
