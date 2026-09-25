# 繁體中文翻譯詞彙表

翻譯 `i18n/zh-TW/docs/` 時使用的統一譯名，採台灣慣用語。英文文件是唯一的
正本，譯文可以落後，但不應與英文內容矛盾。

## 原則

- 程式碼、識別字、參數名稱、HTTP 標頭與指令保持原文，並以 inline code 標示：
  `@cache`、`ttl`、`Cache-Control`、`no-store`、`If-None-Match`。
- 程式碼區塊內的程式碼不翻譯，只翻譯註解。
- 中文與英文、數字、inline code 之間加一個半形空格；中文句子使用全形標點。
- 下表標示「保留」的詞在中文句子中直接使用英文。
- 章節錨點（`#authenticated-endpoints` 等）由英文標題產生；連到英文文件時沿用英文錨點。

## 詞彙

| 英文 | 譯名 | 備註 |
|------|------|------|
| cache（名詞） | 快取 | |
| cache（動詞） | 快取、存入快取 | |
| cache hit / miss | 快取命中／未命中 | |
| cache key | 快取鍵 | |
| backend | 後端 | |
| header | 標頭 | |
| request / response | 請求／回應 | |
| handler | 保留 | 指路由處理函式 |
| route / endpoint | 路由／端點 | |
| decorator | 裝飾器 | |
| dependency | 依賴項 | FastAPI 的 dependency injection：依賴注入 |
| middleware | 中介軟體 | |
| directive | 指令 | `Cache-Control` 指令 |
| revalidation | 重新驗證 | |
| invalidate / invalidation | 使……失效／快取失效 | |
| expire / expiration | 過期 | |
| sliding expiration | 滑動過期 | |
| TTL | 保留 | |
| prefix / namespace | 前綴／命名空間 | |
| atomic | 原子性、原子操作 | |
| counter | 計數器 | |
| lock | 鎖 | |
| token | 權杖 | |
| signature / sign | 簽章／簽署 | |
| session | 保留（Session） | |
| claim | 保留 | JWT claim |
| state（OAuth） | 保留 | |
| serialize / deserialize | 序列化／反序列化 | |
| extra | 保留 | 套件的選用依賴，例：`redis` extra |
| deprecated | 已棄用 | |
| breaking change | 破壞性變更 | |
