# Coding standards

Review 時使用（`/code-review` 的 Standards 一軸）。可以機械式檢查的規則由 `tools/check.sh` 執行，不列在這裏。用詞以 `GLOSSARY.md` 為準。

## 失敗與輸出

- **先驗證，後寫入。** 輸入或設定驗證失敗時以非 0 結束，不寫出任何部分輸出。範例：`render_ground_truth` 的 `main`。
  Why：部分輸出會被下一步當成完整結果，報告就會靜默出錯。
- **不靜默丟掉東西。** 不認識的格式、type 或回應一律記錄下來，令指令失敗，或標為 `error`；不當作成功，也不略過。範例：轉換器的 `meta.unknown` 與 `verify`。
  Why：這個專案最大的風險是「漏了而不知道」，例如漏段、漏記 Block。
- **終端輸出只印 id、類型和數量。** 文章內容、譯文和事實留在 `data/` 的檔案；log 和錯誤訊息不含 key。範例：`cmd_verify`。
  Why：終端輸出會進入 agent 的 session 和 shell 歷史。
- 輸出預設寫在 `data/`。設定檔、prompt 範本和測試不含真實文章內容。

## 結構

- **對外呼叫只經 transport。** Provider adapter 只負責組請求和解讀回應；重試、續跑、記錄放在共用的一層，不在各個 adapter 重複實作。
- **schema 只定義一次。** 檔案格式以 pydantic model 為唯一來源；其他地方引用 model，不重抄欄位名單或 enum。
- **判定與分數由程式按規則計算。** Judge 的輸出只是證據：LLM 給出的文字要經過逐字驗證或 schema 驗證，才可以成為 Finding、verdict 或結果類別。
- 命名、訊息和報告都用 GLOSSARY 的詞，不可混用，例如 Finding 與 Warning、Block 與 Refusal、Run 與 Judge 的兩次呼叫。

## 測試

- 從指令的 `main(argv)` 進入，配合暫存目錄、虛構資料和假 transport。斷言結束碼、輸出檔和送出的請求，不斷言內部函式或中間資料結構。
- 只用虛構資料：虛構標題與人名，案件編號用 2030 年或以後。看起來像真實資料或 key 的字串，在執行時才拼接。
- 每條 Deterministic check 規則、每種 provider 回應，都至少有一個測試。

## 風格

- 跟周圍的程式一致：docstring 和註解用中文短句，說明「為甚麼」，不重述程式在做甚麼；檔案開頭的 docstring 寫明用法。
