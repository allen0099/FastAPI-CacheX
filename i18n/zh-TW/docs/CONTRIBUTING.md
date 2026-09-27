# 貢獻 FastAPI-CacheX {#contributing-to-fastapi-cachex}

我們很歡迎你的參與！我們希望讓貢獻 FastAPI-CacheX 盡可能簡單而透明，無論是：

- 回報錯誤
- 討論程式碼的現況
- 提交修正
- 提議新功能
- 成為維護者

## 開發流程 {#development-process}

1. Fork 這個專案
2. 建立你的功能分支（`git checkout -b feature/AmazingFeature`）
3. 提交你的變更（`git commit -m 'Add some AmazingFeature'`）
4. 推送到該分支（`git push origin feature/AmazingFeature`）
5. 開啟 Pull Request

## 開發環境設定 {#development-setup}

設定開發環境的詳細步驟，請參考[開發指南](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/)（英文）。

> [!WARNING]
> Redis 與 Memcached 的測試會清空它們連線的伺服器，因此除非你以 `CACHEX_TEST_REDIS_PORT`／`CACHEX_TEST_MEMCACHED_PORT` 指定連接埠，否則這些測試會被略過。請讓它們連到用完即丟的容器，絕對不要連到你想保留資料的伺服器，詳見 [Redis 與 Memcached 測試需主動啟用](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#redis-and-memcached-tests-are-opt-in)（英文）。

## Pull Request 流程 {#pull-request-process}

1. 變更介面時，請更新 `docs/` 底下對應的指南（若變更應出現在首頁，也請更新 README）。只有英文頁面需要更新：[繁體中文翻譯](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#traditional-chinese-translation)（英文）允許落後於英文版。
2. 若你的變更改變了行為、新增了公開 API，或修正了使用者可能遇到的問題，請在 [CHANGELOG.md](https://github.com/allen0099/FastAPI-CacheX/blob/master/CHANGELOG.md) 的 `## [Unreleased]` 段落新增一筆項目。項目以粗體的一行摘要開頭，寫成 `- **What changed.** The details...`：發行說明只會列出這些摘要，缺少摘要的項目會讓 CI 失敗。發行時若該段落是空的，發行流程也會拒絕執行，因此遺漏終究會被發現，但要到發行時才會發現，而且只會知道「有人忘了寫」，無法得知是哪個 PR。
3. 為任何新的依賴、功能或變更更新文件。新的公開 API 需要 docstring；若它位於尚未涵蓋的模組中，還需要在 `docs/api/` 底下新增項目。請以 `uv run zensical build --strict` 檢查網站（見[文件網站](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#documentation-site)（英文））。
4. 取得至少一位其他開發者的同意後，PR 即可合併。

## 有任何問題？ {#any-questions}

如果需要任何協助，歡迎開一個帶有 `question` 標籤的 issue！
