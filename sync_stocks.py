"""
同步上市、上櫃公司基本資料到 Supabase 的 stocks 表。

資料來源(證交所 / 櫃買中心 OpenAPI,免 key):
  - 上市公司基本資料:欄位 公司代號 / 公司簡稱 / 產業別
  - 上櫃公司基本資料:欄位 SecuritiesCompanyCode / CompanyAbbreviation / SecuritiesIndustryCode

這支腳本做的事:
  1. 抓兩份資料,轉成統一格式(代號、簡稱、產業別代碼)
  2. 依產業別代碼 + 手動清單算出 in_scope
  3. 分批 upsert 到 stocks 表

注意:寫入的欄位「不包含 themes」,所以每次同步不會蓋掉你手動維護的主題分組。

環境變數:
  SUPABASE_URL / SUPABASE_SERVICE_KEY   跟 fetch_tw_data.py 一樣
  DRY_RUN=1                             只抓資料、印出結果,不寫入(第一次建議先這樣跑)
  TWSE_COMPANY_URL / TPEX_COMPANY_URL   (選填)覆蓋預設的 API 網址

第一次使用前,請先在 Supabase 執行:
  alter table public.stocks add column industry_name text null;
"""

import os
import sys
import json
import time
import datetime
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

# 這兩個網址是憑印象寫的預設值,沒有查證過。
# 如果抓不到,請到 OpenAPI 的 Swagger 頁面按 Execute,複製畫面上的 "Request URL",
# 用環境變數 TWSE_COMPANY_URL / TPEX_COMPANY_URL 覆蓋即可,不用改程式。
TWSE_COMPANY_URL = os.environ.get(
    "TWSE_COMPANY_URL", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
)
TPEX_COMPANY_URL = os.environ.get(
    "TPEX_COMPANY_URL", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
)

HEADERS_SUPABASE = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates",
}

BATCH_SIZE = 500

# 防呆:抓回來的筆數太少,代表回應可能不完整,直接中止,不寫入
MIN_TWSE_ROWS = 800
MIN_TPEX_ROWS = 600

# ---------------------------------------------------------------------------
# 範圍設定(要調整追蹤範圍,改這一區就好)
# ---------------------------------------------------------------------------

# 納入追蹤的產業別代碼
#   已由樣本資料確認: 24 半導體業 / 15 航運業 / 17 金融保險業
#   以下 25~31 是憑印象列的電子類代碼,尚未核對,資料載入後請用核對查詢確認
IN_SCOPE_INDUSTRY_CODES = {
    "24", "25", "26", "27", "28", "29", "30", "31",  # 電子類(25~31 待核對)
    "15",  # 航運業(我印象中也包含航空公司,載入後看清單再排除)
    "17",  # 金融保險業
}

# 不論產業別,強制納入(例如被歸在其他產業的上游材料、設備股)
INCLUDE_SYMBOLS = set()

# 不論產業別,強制排除(例如航運業裡的航空股)。排除的優先權高於納入。
EXCLUDE_SYMBOLS = set()

# 產業別代碼 -> 中文名稱。目前只放已確認的,其他先留空,核對後再補。
INDUSTRY_NAMES = {
    "24": "半導體業",
    "15": "航運業",
    "17": "金融保險業",
}

# ---------------------------------------------------------------------------


def clean(v):
    """轉成去掉前後空白的字串,None 轉成空字串"""
    if v is None:
        return ""
    return str(v).strip()


def fetch_json(url, label):
    """下載 JSON 清單,失敗會重試 3 次,最後仍失敗就丟出例外"""
    last_err = None
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            # utf-8-sig 可以處理開頭有 BOM 的情況
            data = json.loads(resp.content.decode("utf-8-sig"))
            if not isinstance(data, list) or not data:
                raise ValueError("回傳內容不是有資料的 JSON 清單")
            print(f"[{label}] 下載成功,共 {len(data)} 筆")
            return data
        except (requests.RequestException, ValueError) as e:
            last_err = e
            print(f"[{label}] 第 {attempt} 次失敗: {e}")
            if attempt < 3:
                time.sleep(3 * attempt)
    raise RuntimeError(f"[{label}] 下載失敗,網址: {url},最後的錯誤: {last_err}")


def parse_twse(rows):
    out = []
    for r in rows:
        symbol = clean(r.get("公司代號"))
        if not symbol:
            continue
        out.append({
            "symbol": symbol,
            "name": clean(r.get("公司簡稱")) or clean(r.get("公司名稱")),
            "industry": clean(r.get("產業別")),
        })
    return out


def parse_tpex(rows):
    out = []
    for r in rows:
        symbol = clean(r.get("SecuritiesCompanyCode"))
        if not symbol:
            continue
        out.append({
            "symbol": symbol,
            "name": clean(r.get("CompanyAbbreviation")) or clean(r.get("CompanyName")),
            "industry": clean(r.get("SecuritiesIndustryCode")),
        })
    return out


def decide_in_scope(symbol, industry):
    if symbol in EXCLUDE_SYMBOLS:
        return False
    if symbol in INCLUDE_SYMBOLS:
        return True
    return industry in IN_SCOPE_INDUSTRY_CODES


def build_rows(items):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows = []
    for it in items:
        industry = it["industry"]
        rows.append({
            "symbol": it["symbol"],
            "market": "TW",
            "name": it["name"],
            "industry": industry or None,
            "industry_name": INDUSTRY_NAMES.get(industry),
            "in_scope": decide_in_scope(it["symbol"], industry),
            "updated_at": now,
            # 刻意不放 themes,避免蓋掉手動維護的主題分組
        })
    return rows


def upsert_stocks(rows):
    url = f"{SUPABASE_URL}/rest/v1/stocks?on_conflict=symbol,market"
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        resp = requests.post(url, headers=HEADERS_SUPABASE, json=batch, timeout=60)
        if resp.status_code >= 300:
            print("Supabase 寫入失敗:", resp.status_code, resp.text)
            resp.raise_for_status()
        print(f"成功寫入第 {i + 1}~{i + len(batch)} 筆")


def main():
    if not DRY_RUN and (not SUPABASE_URL or not SUPABASE_SERVICE_KEY):
        print("錯誤:缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY 環境變數")
        sys.exit(1)

    twse = parse_twse(fetch_json(TWSE_COMPANY_URL, "上市"))
    tpex = parse_tpex(fetch_json(TPEX_COMPANY_URL, "上櫃"))

    if len(twse) < MIN_TWSE_ROWS or len(tpex) < MIN_TPEX_ROWS:
        print(f"錯誤:筆數異常(上市 {len(twse)}、上櫃 {len(tpex)}),回應可能不完整,中止,未寫入任何資料")
        sys.exit(1)

    merged = {}
    duplicated = 0
    for it in twse + tpex:
        if it["symbol"] in merged:
            duplicated += 1
        merged[it["symbol"]] = it
    if duplicated:
        print(f"注意:有 {duplicated} 個代號同時出現在上市與上櫃資料裡,以上櫃為準")

    rows = build_rows(merged.values())
    in_scope_rows = [r for r in rows if r["in_scope"]]
    print(f"\n上市 {len(twse)} 檔、上櫃 {len(tpex)} 檔,合計 {len(rows)} 檔;納入追蹤 {len(in_scope_rows)} 檔")

    by_code = {}
    for r in in_scope_rows:
        by_code[r["industry"]] = by_code.get(r["industry"], 0) + 1
    for code in sorted(by_code, key=lambda c: (c is None, c)):
        print(f"  產業別 {code}: {by_code[code]} 檔")

    if DRY_RUN:
        print("\nDRY_RUN=1,不寫入。前 5 筆納入追蹤的股票:")
        for r in in_scope_rows[:5]:
            print(f"  {r['symbol']} {r['name']} (產業別 {r['industry']})")
        return

    upsert_stocks(rows)
    print("\nstocks 表同步完成")


if __name__ == "__main__":
    main()
