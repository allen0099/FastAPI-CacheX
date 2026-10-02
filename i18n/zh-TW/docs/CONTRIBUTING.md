# 貢獻 FastAPI-CacheX {#contributing-to-fastapi-cachex}

我們很歡迎你的參與！我們希望讓貢獻 FastAPI-CacheX 盡可能簡單而透明，無論是：

- 回報錯誤
- 討論程式碼的現況
- 提交修正
- 提議新功能
- 成為維護者

## 回報安全性漏洞 {#reporting-a-security-vulnerability}

請不要在公開的 issue 或 Pull Request 中回報安全性問題，而是透過 [GitHub 私下漏洞回報](https://github.com/allen0099/FastAPI-CacheX/security/advisories/new)私下回報。支援的版本以及回報應包含的內容，請見[安全性政策](https://github.com/allen0099/FastAPI-CacheX/blob/master/SECURITY.md)（英文）。

## 開啟 Pull Request 之前 {#before-you-open-a-pull-request}

外部貢獻者的 Pull Request 要從 issue 開始：

1. 找到或開一個對應這項變更的 issue，並在上面留言說明你想處理它。
2. 等維護者把該 issue 指派給你。
3. 開啟 Pull Request，並在描述中寫上 `Fixes #<issue>`。

修改 `fastapi_cachex/` 底下的程式時，還需要在 `tests/` 底下加上測試，並附上 changelog 片段（見 [Pull Request 流程](#pull-request-process)）。

名為 **PR gate** 的檢查會對外部貢獻者執行這些規則；維護者、協作者，以及 Renovate 等機器人不受此限制。Pull Request 沒有關閉一個指派給作者本人的 issue 時，它會關閉該 Pull Request。編輯已關閉的 Pull Request 不會讓它重新開啟，因此請在 issue 指派給你之後開一個新的 Pull Request。如果只缺少測試或 changelog 片段，Pull Request 會保持開啟，檢查會失敗並留言列出缺少的項目，之後每次推送或編輯都會重新檢查。只要修改了 `fastapi_cachex/` 底下的程式，這項檢查就會要求 changelog 片段；若變更不需要片段（例如重構），維護者可以加上 `skip-pr-gate` 標籤略過檢查。維護者要重新開啟被檢查關閉的 Pull Request 時，請先加上該標籤，否則檢查會再次關閉它。

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
2. 若你的變更改變了行為、新增了公開 API，或修正了使用者可能遇到的問題，請新增一個 changelog 片段：檔案 `changelog.d/<issue>.<section>.md`（section 為 `added`、`changed`、`deprecated`、`removed`、`fixed` 或 `security`），內容為該筆項目，以粗體的一行摘要開頭，寫成 `**What changed.** The details...`，不要加上開頭的 `- ` 或 issue 連結——發行時會自動補上。請不要直接編輯 [CHANGELOG.md](https://github.com/allen0099/FastAPI-CacheX/blob/master/CHANGELOG.md)，詳見 [Changelog 片段](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#changelog-fragments)（英文）。發行說明只會列出這些摘要，格式錯誤的片段會讓 CI 失敗。沒有任何可發行的項目時，發行流程也會拒絕執行，因此遺漏終究會被發現，但要到發行時才會發現，而且只會知道「有人忘了寫」，無法得知是哪個 PR。
3. 為任何新的依賴項、功能或變更更新文件。新的公開 API 需要 docstring；若它位於尚未涵蓋的模組中，還需要在 `docs/api/` 底下新增項目。請以 `uv run zensical build --strict` 檢查網站（見[文件網站](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#documentation-site)（英文））。
4. 取得至少一位其他開發者的同意後，PR 即可合併。

## 有任何問題？ {#any-questions}

如果需要任何協助，歡迎開一個帶有 `question` 標籤的 issue！
