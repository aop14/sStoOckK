"""
每日抓取台股「三大法人買賣超」+「收盤行情」,寫入 Supabase。

資料來源(證交所官方公開 JSON,免 key):
  - T86:      https://www.twse.com.tw/rwd/zh/fund/T86
  - MI_INDEX: https://www.twse.com.tw/exchangeReport/MI_INDEX

這兩個端點每次呼叫會回傳「當天全部上市股票」的資料,所以不管
watchlist 裡有幾檔股票、怎麼增減,每天都只需要各呼叫一次。

需要的環境變數(在 GitHub Actions 的 Secrets 設定):
  SUPABASE_URL          你的 Supabase 專案 URL
  SUPABASE_SERVICE_KEY  service_role key(不是 anon key!有寫入權限,
                         千萬不要放進前端或公開的地方)
"""

import os
import sys
import datetime
import requests

TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_MI_INDEX_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
TWSE_MI_QFIIS_URL = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

HEADERS_SUPABASE = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates",
}


def to_num(s):
    """把 '1,234' / '' / '--' 這類字串轉成 float,轉不了就回傳 None"""
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "--", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def get_active_tw_symbols():
    """從 Supabase watchlist 讀取目前要追蹤的台股代號"""
    url = f"{SUPABASE_URL}/rest/v1/watchlist"
    params = {"market": "eq.TW", "is_active": "eq.true", "select": "symbol"}
    resp = requests.get(url, headers=HEADERS_SUPABASE, params=params, timeout=30)
    resp.raise_for_status()
    return {row["symbol"] for row in resp.json()}


def fetch_t86(date_str):
    """三大法人買賣超日報,回傳 {股票代號: {foreign_net, trust_net, dealer_net, institutional_net}}"""
    params = {"response": "json", "date": date_str, "selectType": "ALLBUT0999"}
    resp = requests.get(TWSE_T86_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("stat") != "OK":
        print(f"[T86] {date_str} 無資料(可能是非交易日): {data.get('stat')}")
        return {}

    result = {}
    for row in data.get("data", []):
        code = row[0].strip()
        # 欄位順序(2018年後上市格式):
        # 0 證券代號 1 證券名稱
        # 2 外陸資買進 3 外陸資賣出 4 外陸資買賣超(不含外資自營商)
        # 5 外資自營商買進 6 外資自營商賣出 7 外資自營商買賣超
        # 8 投信買進 9 投信賣出 10 投信買賣超
        # 11 自營商買賣超(合計) 12~17 自營商細項
        # 18 三大法人買賣超股數合計
        foreign_net_1 = to_num(row[4]) or 0
        foreign_net_2 = to_num(row[7]) or 0
        result[code] = {
            "foreign_net": int(foreign_net_1 + foreign_net_2),
            "trust_net": int(to_num(row[10]) or 0),
            "dealer_net": int(to_num(row[11]) or 0),
            "institutional_net": int(to_num(row[18]) or 0),
        }
    return result


def fetch_mi_index(date_str):
    """每日收盤行情,回傳 {股票代號: {close_price, change_percent, volume}}"""
    params = {"response": "json", "date": date_str, "type": "ALLBUT0999"}
    resp = requests.get(TWSE_MI_INDEX_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("stat") != "OK":
        print(f"[MI_INDEX] {date_str} 無資料(可能是非交易日): {data.get('stat')}")
        return {}

    # MI_INDEX 回傳好幾個 table,股票明細通常在 fields9/data9 或用 tables 結構,
    # 這裡採用較新版本的 "tables" 格式,找出欄位含「收盤價」的那個表。
    result = {}
    tables = data.get("tables") or [data]  # 舊版沒有 tables 包一層
    for table in tables:
        fields = table.get("fields") or []
        if "收盤價" not in fields or "證券代號" not in fields:
            continue
        idx_code = fields.index("證券代號")
        idx_close = fields.index("收盤價")
        idx_diff = fields.index("漲跌價差") if "漲跌價差" in fields else None
        idx_vol = fields.index("成交股數") if "成交股數" in fields else None
        idx_open = fields.index("開盤價") if "開盤價" in fields else None
        idx_high = fields.index("最高價") if "最高價" in fields else None
        idx_low = fields.index("最低價") if "最低價" in fields else None
        idx_pe = fields.index("本益比") if "本益比" in fields else None

        for row in table.get("data", []):
            code = row[idx_code].strip()
            close = to_num(row[idx_close])
            if close is None:
                continue
            diff = to_num(row[idx_diff]) if idx_diff is not None else None
            change_percent = None
            if diff is not None and (close - diff) != 0:
                change_percent = round(diff / (close - diff) * 100, 2)
            volume = to_num(row[idx_vol]) if idx_vol is not None else None

            result[code] = {
                "close_price": close,
                "change_percent": change_percent,
                "volume": int(volume) if volume is not None else None,
                "open_price": to_num(row[idx_open]) if idx_open is not None else None,
                "high_price": to_num(row[idx_high]) if idx_high is not None else None,
                "low_price": to_num(row[idx_low]) if idx_low is not None else None,
                "pe_ratio": to_num(row[idx_pe]) if idx_pe is not None else None,
            }
    return result


def fetch_mi_qfiis(date_str):
    """外資及陸資持股比率 + 尚可投資比率
    回傳 {股票代號: {holding_ratio, investable_ratio}}

    欄位名稱證交所偶爾會微調,所以用「欄位名稱包含關鍵字」這種方式動態找欄位,
    而不是寫死欄位索引,比較不怕格式變動。
    """
    params = {"response": "json", "date": date_str, "selectType": "ALLBUT0999"}
    resp = requests.get(TWSE_MI_QFIIS_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("stat") != "OK":
        print(f"[MI_QFIIS] {date_str} 無資料(可能是非交易日): {data.get('stat')}")
        return {}

    result = {}
    tables = data.get("tables") or [data]
    for table in tables:
        fields = table.get("fields") or []
        code_field_candidates = [f for f in fields if f in ("證券代號", "代號")]
        holding_field_candidates = [
            f for f in fields
            if "持股比率" in f and "上限" not in f and "尚可" not in f
        ]
        investable_field_candidates = [
            f for f in fields
            if "尚可" in f and "比率" in f
        ]
        if not code_field_candidates or not holding_field_candidates:
            continue

        idx_code = fields.index(code_field_candidates[0])
        idx_holding = fields.index(holding_field_candidates[0])
        idx_investable = fields.index(investable_field_candidates[0]) if investable_field_candidates else None

        for row in table.get("data", []):
            code = row[idx_code].strip()
            holding = to_num(row[idx_holding])
            investable = to_num(row[idx_investable]) if idx_investable is not None else None
            if holding is not None:
                result[code] = {
                    "holding_ratio": holding,
                    "investable_ratio": investable,
                }
    return result


def upsert_daily_data(rows):
    if not rows:
        print("沒有資料要寫入")
        return
    url = f"{SUPABASE_URL}/rest/v1/daily_data?on_conflict=symbol,market,date"
    resp = requests.post(url, headers=HEADERS_SUPABASE, json=rows, timeout=60)
    if resp.status_code >= 300:
        print("Supabase 寫入失敗:", resp.status_code, resp.text)
        resp.raise_for_status()
    print(f"成功寫入 {len(rows)} 筆資料")


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("錯誤:缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY 環境變數")
        sys.exit(1)

    # 用台北時區的「今天」當查詢日期;GitHub Actions 的排程時間已經對齊台股收盤後
    today = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    date_str = today.strftime("%Y%m%d")

    symbols = get_active_tw_symbols()
    if not symbols:
        print("watchlist 裡沒有啟用中的台股,請先在 Supabase 的 watchlist 表加入股票")
        return
    print(f"追蹤中的台股: {sorted(symbols)}")

    t86 = fetch_t86(date_str)
    mi = fetch_mi_index(date_str)
    qfiis = fetch_mi_qfiis(date_str)

    if not t86 and not mi:
        print(f"{date_str} 兩個來源都沒有資料,可能是假日或尚未收盤更新,結束。")
        return

    rows = []
    for symbol in symbols:
        inst = t86.get(symbol, {})
        price = mi.get(symbol, {})
        if not inst and not price:
            print(f"  {symbol}: 今天沒抓到資料(代號是否正確?或非交易日)")
            continue
        rows.append({
            "symbol": symbol,
            "market": "TW",
            "date": today.strftime("%Y-%m-%d"),
            "close_price": price.get("close_price"),
            "change_percent": price.get("change_percent"),
            "volume": price.get("volume"),
            "open_price": price.get("open_price"),
            "high_price": price.get("high_price"),
            "low_price": price.get("low_price"),
            "pe_ratio": price.get("pe_ratio"),
            "foreign_net": inst.get("foreign_net"),
            "trust_net": inst.get("trust_net"),
            "dealer_net": inst.get("dealer_net"),
            "institutional_net": inst.get("institutional_net"),
            "foreign_holding_ratio": qfiis.get(symbol, {}).get("holding_ratio"),
            "foreign_investable_ratio": qfiis.get(symbol, {}).get("investable_ratio"),
        })

    upsert_daily_data(rows)


if __name__ == "__main__":
    main()
