# 13: 與 baseline 比較及摘要版報告

**What to build:** 報告把每個 Approach 的分數與 baseline 作比較：質素以方案 A 為 baseline，繁→簡另外與方案 E 比較，而且只比較 baseline 沒有被 Block 的文章。另外輸出一份只有數字和 id 的摘要版，可以交給 Ivan／Stephen。報告開頭補齊所有重現結果需要的資訊。詳見 spec 第 7 和第 10 節。

**Blocked by:** 05, 09

**Status:** ready-for-agent

- [ ] 比較只包括 baseline 所有 Run 都是 `ok` 的文章；多個 Run 時取平均；列出比較所用的文章數
- [ ] 質素的 baseline 是方案 A；繁→簡另外與方案 E 比較
- [ ] 摘要版只含數字、文章 id 和 Component id；測試在輸入中放入虛構的原文、譯文和事實文字，確認這些字串都沒有出現在摘要版中
- [ ] 報告開頭列出：ground truth 的狀態和 hash、Judge 設定、權重、正規化方式、baseline 標籤、同家族例外、審閱完成度
