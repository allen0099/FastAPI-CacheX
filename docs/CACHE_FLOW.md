# FastAPI-CacheX 快取流程說明

本文件詳細說明 FastAPI-CacheX 如何處理 HTTP 請求的快取邏輯。

## 整體流程圖

```
HTTP 請求到達
    ↓
@cache 裝飾器攔截
    ↓
生成快取金鑰: method:host:path:query_params
    ↓
檢查是否有 no-cache 或 no-store 指令
    ├─ 有 no-cache → 跳過快取直接執行處理器
    ├─ 有 no-store → 跳過快取直接執行處理器
    └─ 都無 → 繼續檢查快取
    ↓
查詢快取後端
    ├─ 快取命中 (在 TTL 內)
    │   ├─ 檢查 If-None-Match 標頭
    │   │   ├─ 匹配 ETag → 返回 304 Not Modified
    │   │   └─ 不匹配或無標頭 → 返回 200 OK + 快取內容
    │   └─ (重要: 端點處理器函數 **不執行**)
    └─ 快取未命中或已過期
        ├─ 執行端點處理器函數
        ├─ 取得回應內容
        ├─ 提取 Cache-Control 指令
        ├─ 儲存至快取後端
        └─ 返回回應給客戶端
```

## 詳細步驟

### 1. 請求攔截與金鑰生成

當請求到達時，`@cache` 裝飾器會：

```python
# 快取金鑰格式
cache_key = f"{req.method}:{host}:{req.url.path}:{query_params}"

# 例如：
# GET:example.com:/api/users?page=1&limit=10
# POST:api.example.com:/api/users/123
```

金鑰格式確保不同維度的資料獨立快取：
- **方法隔離**：GET 和 POST 不共享快取
- **主機隔離**：`example.com` 和 `api.example.com` 分別快取
- **路徑隔離**：不同端點各自快取
- **查詢參數隔離**：同一端點不同查詢參數分別快取

### 2. Cache-Control 指令檢查

裝飾器檢查各種快取指令：

```python
# 完全跳過快取
@cache(no_cache=True)     # 強制重新驗證
@cache(no_store=True)     # 不儲存任何內容

# 正常快取行為
@cache(ttl=3600)          # 快取 1 小時
@cache(max_age=300)       # 快取 5 分鐘
@cache(public=True)       # 允許共享快取
@cache(private=True)      # 僅私有快取
@cache(immutable=True)    # 內容永不變更
```

### 3. 快取查詢

根據快取金鑰查詢後端：

#### 快取命中場景

```python
# 假設時間: 14:30:00，快取項目:
cached_item = {
    'etag': '"abc123"',
    'content': b'{"data": "response"}',
    'expires_at': 14:31:30  # 60 秒 TTL，仍有效
}

# 客戶端請求
GET /api/endpoint
If-None-Match: "abc123"
```

**決策邏輯**：

```python
if not in_ttl:
    # 快取已過期
    execute_handler()
elif if_none_match_header:
    if if_none_match == cached_etag:
        return 304 Not Modified  # 帶寬優化
    else:
        return 200 OK + cached_content  # 內容更新
else:
    # 無 If-None-Match 標頭但快取有效
    return 200 OK + cached_content  # 快速響應
```

#### 快取未命中場景

```python
# 快取不存在或已過期
execute_handler()
  ↓
# 取得回應
response = await handler()
  ↓
# 提取快取指令
ttl = extract_cache_control(response)
  ↓
# 生成 ETag (如果響應有 content)
etag = generate_etag(response.body)
  ↓
# 儲存至後端
await backend.set(
    key=cache_key,
    value=CacheItem(
        etag=etag,
        content=response.body,
        expires_at=now + ttl
    )
)
  ↓
return response
```

### 4. ETag 生成與驗證

ETag 用於驗證內容是否變更：

```python
# 生成 ETag (MD5 哈希)
import hashlib

def generate_etag(content: bytes) -> str:
    hash_obj = hashlib.md5(content)
    return f'"{hash_obj.hexdigest()}"'

# 驗證
If-None-Match: "abc123"  # 客戶端發送之前的 ETag
↓
if "abc123" == cached_etag:
    return 304  # 內容未變更，客戶端使用本地副本
else:
    return 200 + new_content  # 內容已變更，發送新內容
```

## 後端存儲格式

### MemoryBackend

```python
{
    'cache_key_1': CacheItem(
        etag='"abc123"',
        content=b'...',
        expires_at=1702650600.5
    ),
    'cache_key_2': { ... }
}

# 特點：
# - 儲存於進程內記憶體
# - 自動清理任務每 60 秒運行
# - 過期項目被移除
```

### MemcachedBackend

```
# 傳輸格式：JSON 序列化
key: "fastapi_cachex:GET:example.com:/api/users"
value: {
    "etag": "\"abc123\"",
    "content": "base64_encoded_bytes",
    "expires_at": 1702650600.5
}

# 特點：
# - 項目序列化為 JSON
# - 內容編碼為 base64 (二進位安全)
# - Memcached 自動根據到期時間刪除
```

### AsyncRedisCacheBackend

```
# 儲存格式：JSON 序列化
key: "fastapi_cachex:GET:example.com:/api/users"
value: {
    "etag": "\"abc123\"",
    "content": "base64_encoded_bytes",
    "expires_at": 1702650600.5
}

# 特點：
# - 使用 Redis SETEX 自動過期
# - 支援 SCAN 進行模式清除 (高效非阻塞)
# - 支援自訂金鑰前綴 (多租戶)
```

## 快取清除策略

### 自動清除

```python
# MemoryBackend: 每 60 秒自動清理
async def cleanup_task():
    while True:
        await asyncio.sleep(60)
        # 移除 expires_at < now 的所有項目

# Redis/Memcached: TTL 機制
# 使用後端的內置 TTL (SETEX, expire)
# 項目自動過期，無需清理任務
```

### 手動清除

```python
# 清除特定路徑
await cache.clear_path("/api/users")  # 移除所有 host/method/params 組合

# 清除模式
await cache.clear_pattern("/api/users/*")  # 移除所有 /api/users/... 的項目

# 清除全部
await cache.clear()  # 移除所有快取項目
```

## 性能優化

### 快取命中路徑

```
請求 → 快取查詢 (< 5ms)
        ↓
        返回快取 (< 1ms)

總耗時: ~5-10ms (無需執行端點處理器)
相比直接執行: 節省 100-1000ms+ (取決於端點複雜度)
```

### 後端選擇建議

| 場景 | 推薦後端 | 原因 |
|------|--------|------|
| 開發測試 | MemoryBackend | 快速、無依賴 |
| 分散式系統 | Redis | 非同步、高效、支援模式清除 |
| 簡單快取 | Memcached | 穩定、成熟 |
| 多進程部署 | Redis | 共享快取、一致性 |

## 快取失效場景

| 場景 | 行為 |
|------|------|
| no-cache 指令 | 跳過快取，重新執行端點 |
| no-store 指令 | 不使用快取，也不儲存 |
| 快取過期 (TTL 到期) | 重新執行端點 |
| must-revalidate 後過期 | 必須重新驗證 |
| 手動調用 clear_path() | 特定路徑快取被清除 |

## 實現細節

### 快取項目結構

```python
class CacheItem(TypedDict):
    etag: str                    # ETag 標籤 (MD5 哈希)
    content: bytes               # 回應內容
    expires_at: Optional[float]  # 過期時間戳 (秒)
```

### 請求流程代碼示例

```python
@cache(ttl=3600)
async def expensive_endpoint():
    # 此函數只在快取未命中時執行
    return await perform_calculation()

# 裝飾器內部邏輯
async def wrapper(request: Request) -> Response:
    # 1. 生成金鑰
    cache_key = generate_key(request)

    # 2. 檢查指令
    if no_cache or no_store:
        return await handler()

    # 3. 查詢快取
    cached = await backend.get(cache_key)

    # 4. 命中邏輯
    if cached and cached.expires_at > now:
        if request.headers.get('If-None-Match') == cached.etag:
            return 304 Not Modified
        return Response(cached.content, status_code=200)

    # 5. 未命中邏輯
    response = await handler()
    etag = generate_etag(response.body)
    ttl = extract_ttl(response)

    # 6. 儲存
    await backend.set(
        cache_key,
        CacheItem(
            etag=etag,
            content=response.body,
            expires_at=now + ttl
        )
    )

    return response
```

## 常見問題

**Q: 為何快取命中不返回 200？**
A: 不一定。如果請求帶有 `If-None-Match` 標頭且 ETag 匹配，返回 304 以節省帶寬。無標頭時返回 200 和內容。

**Q: 為何同一端點有多個快取項目？**
A: 因為快取金鑰包含查詢參數。`/users?page=1` 和 `/users?page=2` 是不同的快取。

**Q: MemoryBackend 如何在多進程中工作？**
A: 不工作。每個進程有獨立快取，推薦生產環境使用 Redis。

**Q: 快取清除是同步還是非同步？**
A: 異步操作。`await cache.clear_path(...)` 或 `await cache.clear_pattern(...)`
