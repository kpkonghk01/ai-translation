# 07: 阿里雲 DashScope 與方案 B

**What to build:** 方案 B（阿里雲 Qwen）可以和其他 Approach 用同一條流程執行，只會送到 International（新加坡）endpoint，平台內容審查的錯誤記為 Block。程式現在就可以做完並以假 transport 測試；真正執行要等 whitelist 批出，以及 Pan 提供 `DASHSCOPE_API_KEY`。詳見 spec 第 3 節。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 只接受新加坡 endpoint；設定中寫其他 endpoint 時，指令被拒絕
- [ ] 內容審查錯誤（例如 `DataInspectionFailed`）→ `block`，並附原因；解讀規則以官方錯誤碼文件為準，並寫成回應樣本
- [ ] 只有在 Approach 設定明確開啟時才送 `X-DashScope-DataInspection` header
- [ ] key 只從 `DASHSCOPE_API_KEY` 讀取
- [ ] 報告中方案 B 的 Block 率附原因分佈，讓人分辨平台審查和其他錯誤
- [ ] whitelist 批出前是否先跑一小批，由 Pan 決定
