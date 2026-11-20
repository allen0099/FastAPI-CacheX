# 繁體中文翻譯詞彙表

翻譯 `docs/zh-TW/` 時使用的統一譯名，採台灣慣用語。英文文件是唯一的
正本，譯文可以落後，但不應與英文內容矛盾。

## 原則

- 程式碼、識別字、參數名稱、HTTP 標頭與指令保持原文，並以 inline code 標示：
  `@cache`、`ttl`、`Cache-Control`、`no-store`、`If-None-Match`。
- 程式碼區塊內的程式碼不翻譯，只翻譯註解。以 `--8<--` 從 `examples/` 引入的程式碼例外：註解維持英文，改在區塊前後以中文說明。
- 中文與英文、數字、inline code 之間加一個半形空格；中文句子使用全形標點。
- 下表標示「保留」的詞在中文句子中直接使用英文。
- 章節錨點沿用英文：每個翻譯後的標題都以 `{#id}` 指定與英文頁面相同的錨點，例如 `## 快取鍵 {#cache-keys}`，讓兩種語言的連結可以互換。
- 中文段落不要在句中換行：換行在 HTML 中會變成空格，出現在兩個中文字之間。一個段落或清單項目寫在同一行。
- 尚未翻譯的頁面以英文網址連結（`https://fastapi-cachex.readthedocs.io/en/stable/...`），並在連結後加上「（英文）」。

## 詞彙

| 英文 | 譯名 | 備註 |
|------|------|------|
| cache（名詞） | 快取 | |
| cache（動詞） | 快取、存入快取 | |
| cache hit / miss | 快取命中／未命中 | |
| cache key | 快取鍵 | |
| (cache) entry | 項目、快取項目 | |
| key builder | 保留 | |
| backend | 後端 | |
| header | 標頭 | |
| request / response | 請求／回應 | |
| (response) body | 本文 | |
| render（回應） | 產生、重新產生 | |
| replay | 重播 | 重播快取的回應；replay attack：重送攻擊 |
| handler | 保留 | 指路由處理函式 |
| route / endpoint | 路由／端點 | |
| decorator | 裝飾器 | |
| dependency | 依賴項 | FastAPI 的 dependency injection：依賴注入 |
| middleware | 中介軟體 | |
| directive | 指令 | `Cache-Control` 指令 |
| revalidation | 重新驗證 | |
| invalidate / invalidation | 使……失效／快取失效 | |
| expire / expiration | 過期 | |
| stale | 過期 | 超過 `ttl` 但仍保留、可以送出的項目（`serve_stale`、`stale_ttl`）。與 expired（已無法讀取）同譯；句中需要區分兩者時，寫作「過期（stale）」 |
| tag（快取） | 保留 | `@cache(tags=...)`、`invalidate_tags()` 的 tag；不譯為「標籤」 |
| format tag（鍵格式） | 格式標籤 | 快取鍵開頭的 `http:v3`；「標籤」只用在這裡 |
| tag（git） | 保留（git tag） | |
| label（GitHub） | 保留 | issue 與 pull request 的 label |
| coalesce / coalescing | 合併（並行的未命中） | `coalesce=` 參數保留 |
| fail open / fail closed | 保留 | 後端發生錯誤時照常回應／拋出錯誤；初次出現可加註「失敗時放行」 |
| evict / eviction | 淘汰 | 後端因容量或 TTL 移除項目 |
| background task | 背景任務 | Starlette 回應的 `background` |
| sliding expiration | 滑動過期 | |
| TTL | 保留 | |
| prefix / namespace | 前綴／命名空間 | |
| atomic | 原子性、原子操作 | |
| counter | 計數器 | |
| lock | 鎖 | |
| one-shot | 一次性 | |
| glob | 萬用字元（glob） | |
| no-op | 保留 | |
| stampede | 保留（cache stampede） | |
| token | 權杖 | |
| signature / sign | 簽章／簽署 | |
| serializer | 序列化器 | |
| secret key | 密鑰 | |
| stateful / stateless | 有狀態／無狀態 | |
| revoke / revocation | 撤銷 | |
| config / configuration | 設定 | 不用「組態」 |
| proxy / reverse proxy | 保留：proxy／反向 proxy | 與 `BackendProxy` 等類別名稱一致 |
| session | 保留（Session） | |
| claim | 保留 | JWT claim |
| state（OAuth） | 保留 | |
| serialize / deserialize | 序列化／反序列化 | |
| process / multi-process | 行程／多行程 | |
| worker | 保留 | 工作執行緒（worker thread）除外 |
| production | 正式環境 | |
| callback（OAuth） | 回呼（callback） | |
| extra | 保留 | 套件的選用依賴，例：`redis` extra |
| (dependency) floor / lower bound | 最低版本 | 例：`fastapi` 的最低版本 |
| deprecated | 已棄用 | |
| breaking change | 破壞性變更 | |
