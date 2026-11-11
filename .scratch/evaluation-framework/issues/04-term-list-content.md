# 04: Term List 內容（草擬約 50 條）

**What to build:** 從測試集（第一批、中國負面新聞組、CMS 15 篇）草擬一份約 50 條的 Term List，放在 `data/`，供翻譯 prompt 和 Deterministic check 使用。這是內容工作：由 Claude 草擬，Pan 抽查。

**Blocked by:** 03

**Status:** ready-for-human（草擬完成，等 Pan 抽查）

- [x] 涵蓋官員英文名、地名、機構名、法律用語和新聞慣用譯法（例如 非禮 → indecent assault），以及繁→簡的香港專有名詞
- [x] 罪名的英文採用香港法例英文版的用語；人名和機構名盡量以官方來源核對，無法核對的條目加上標記
- [x] 通過 03 的 Term List 驗證
- [ ] 產生一份給 Pan 抽查的清單（已產生：`data/term-list-review.md`）；Pan 修正後的內容已寫回
- [x] ticket 的留言只寫條目數量和類別分佈，不貼條目內容

## Comments

### 2026-11-09 草擬完成

- `data/term-list.json`：54 條。person 12、place 8、organization 12、legal 12、news-idiom 10；有繁→簡譯法的 18 條；「未能核對官方來源」的 8 條（`note` 開頭有標記，抽查時優先看）。legal 類的 severity 是 major，其餘 minor。
- 取材：第一批 60 篇與 CMS 15 篇；每條原文詞都確認在測試集出現過，沒有重覆。`translate --dry-run` 載入並驗證通過。
- 抽查清單：`data/term-list-review.md`（按類別，列出原文詞、各 Direction 的譯法、出現在幾篇、核對來源）。只在本機看。
- 之後補的：Term List 比對是子字串、寧可漏報也不誤報，所以允許譯法要含動詞變化。用 Gold set 的 clean 項目反查時發現 `強姦`、`非禮`、`猥褻侵犯`、`認罪` 缺了常見的變化形（例如 raping、admitted his guilt），已補上；真實模型的譯文不會因此被誤記 Terminology。
- `first-batch` 的實驗設定已加上 `term_list`，所以第一批的翻譯 prompt 會注入原文出現過的條目（Run 紀錄會記下模型看到的那部分的 hash）。

### 2026-11-09 另一個模型（Gemini 3.8）核對「未能核對」的 8 條之後

- 8 條都補上了來源（六四是 Pan 提供的 2019-11-13 公報：記者提問用 Tiananmen June 4th incident，沒有官方譯名）。其中 7 條的來源是香港政府新聞公報（我逐一打開確認：網址存在、英文原文確實用了該詞）和內地交通運輸部規章；只核對了英文用語，中文與英文的逐句對應沒有比對。
- 沒有照它建議的「整段覆蓋」：它的清單會把補好的動詞變化形（admitted his guilt 等）刪掉，Terminology 檢查會誤報。
- 它說得過頭、我無法驗證的：「律政司詞彙庫確認了每一條」（那個詞彙庫沒有固定的單條網址，只能在網頁上搜尋）；`complainant`、`leniency` 在它給的公報裡都沒有出現，所以只當容許寫法，不算已核對。
- 一個實際影響：「的士」的簡體，內地規章用「出租汽车」、「出租车」是通行簡稱，香港運輸署的簡體網頁只用「的士」。允許譯法加了「出租汽车」；譯文若保留「的士」仍會記 Terminology（目標讀者是內地人）。

### 2026-11-11 其餘 46 條交給另一個模型（Gemini 3.8）審查之後

- 它對 9 條提了意見，我只採用 2 條：**李運騰**（備註寫「上訴法庭法官」，與來源的「原訟法庭法官」矛盾，已改備註；備註不進 prompt 也不影響檢查）；**李素蘭**（補全名兩種寫法，新聞稿寫全名，已打開核對；原有的姓氏寫法是全名的子字串，檢查結果不變，只是翻譯 prompt 現在會給出全名）。
- 其餘 7 條沒採用，理由都是它沒考慮到「子字串、不分大小寫」的比對規則，照做會讓檢查變差（我用程式驗證過）：
  - **內地**：建議刪小寫 `mainland`、改列 `the Mainland`。比對不分大小寫，`mainland` 本來就涵蓋 `the Mainland`、`Mainland China`；照它改，譯文寫 `Mainland authorities`、`mainland visitors` 會被誤記。
  - **強姦**：建議刪 `raping`。`rape` 不是 `raping` 的子字串，刪了之後 "raping her" 這類正常譯文會被誤記。
  - **尋釁滋事**：建議刪 `picking quarrels`、加複數寫法。複數已被現有條目涵蓋；`picking quarrels` 是 "…and stirring up trouble" 等各種後半寫法共用的前半，刪了會誤記這些。
  - **中紀委、中央紀委、國家監委、中央軍委**：建議補繁→簡譯法（理由是「體例一致」）。這四個簡體只是字形轉換，補了之後 `中央纪律检查委员会`、`国家监察委员会` 這類正常的全稱會被誤記；繁→簡本來就另有殘留繁體字的檢查。
- 詞彙表至此定稿；真跑之後再改原文詞或譯法，`check` 會拒絕，要換新的實驗 id 重新翻譯。

