# 三大法人籌碼追蹤(台股 v1)

## 這個版本做了什麼
- 每個交易日收盤後,自動抓取你自選股清單裡「台股」的三大法人買賣超 + 收盤行情
- 存進 Supabase
- 手機/電腦都能打開的黑底科技感儀表板,可切換股票、切換日期區間,看股價走勢 + 三大法人買賣超柱狀圖 + 4 張摘要卡片

⚠️ 目前只涵蓋**上市股票(TWSE)**。若自選股裡有上櫃(OTC)股票,證交所這兩個端點抓不到,之後可以再加櫃買中心的資料源。美股價格追蹤(yfinance)也還沒接進來,先把這個做穩再說。

## 設定步驟

### 1. 建立 Supabase 專案(如果還沒有)
在 [supabase.com](https://supabase.com) 建立一個新專案,免費方案就夠用。

### 2. 建立資料表
打開 Supabase 後台的 **SQL Editor**,貼上 `schema.sql` 整個檔案的內容執行一次。

### 3. 把想追蹤的股票加進 watchlist
在 **Table Editor** 找到 `watchlist` 表,手動新增幾筆,例如:

| symbol | market | name | is_active |
|---|---|---|---|
| 2330 | TW | 台積電 | true |
| 2317 | TW | 鴻海 | true |

之後想加減股票,直接在這張表加一列或把 `is_active` 改成 `false` 就好,完全不用碰程式碼。

### 4. 設定 GitHub repo
把這整個資料夾推上一個新的 GitHub repo。

在 repo 的 **Settings → Secrets and variables → Actions** 加兩個 Secret:
- `SUPABASE_URL`:Supabase 後台 Project Settings → API 裡的 Project URL
- `SUPABASE_SERVICE_KEY`:同一頁的 `service_role` key(⚠️ 這把 key 權限很高,只能放在 GitHub Secrets,絕對不要放進 dashboard 的程式碼或任何公開的地方)

### 5. 測試排程
去 repo 的 **Actions** 分頁,找到 `Fetch TW institutional data`,點 **Run workflow** 手動跑一次,確認執行成功、Supabase 的 `daily_data` 表有資料寫入。平常會在每個交易日台北時間 15:30 自動執行,不用手動點。

### 6. 打開儀表板
編輯 `dashboard/index.html`,把最上面的：
```js
const SUPABASE_URL = "YOUR_SUPABASE_URL";
const SUPABASE_ANON_KEY = "YOUR_SUPABASE_ANON_KEY";
```
換成 Project Settings → API 裡的 **Project URL** 和 **anon public key**(這把 key 可以公開放在前端,因為資料表已經用 RLS 設定成「只能讀、不能寫」)。

**怎麼看這個頁面:**
- 最簡單:把 `dashboard/index.html` 用瀏覽器直接打開就能用
- 想要手機隨時看:把 `dashboard` 資料夾用 **GitHub Pages** 部署(repo Settings → Pages → 選 `dashboard` 資料夾),之後就有一個網址,存成手機主畫面捷徑,打開體驗接近 App

## 之後可以擴充的方向
- 加上櫃(OTC)股票的資料來源
- 接入 yfinance 抓美股價格
- 摘要卡片再加投信持股比重、均價乖離等指標
- 用 GitHub Actions 排程做「投信連續買超 N 天」之類的條件通知
