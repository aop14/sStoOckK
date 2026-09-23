"""
回補指定日期區間的台股三大法人 + 收盤資料。
跟 fetch_tw_data.py 用同一套抓取邏輯,只是改成逐日迴圈,
並且在每次呼叫證交所之間加短暫延遲,避免短時間內打太多次請求。

用法(環境變數):
  BACKFILL_START = 2026-07-22   (起始日期,含當天)
  BACKFILL_END   = 2026-09-22   (結束日期,含當天)
  SUPABASE_URL / SUPABASE_SERVICE_KEY 跟 fetch_tw_data.py 一樣

非交易日(週六日、國定假日)證交所會回傳空資料,程式會自動跳過,
不用自己先計算哪幾天有開盤。
"""

import os
import sys
import time
import datetime

from fetch_tw_data import (
    get_active_tw_symbols,
    fetch_t86,
    fetch_mi_index,
    fetch_mi_qfiis,
    upsert_daily_data,
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
)

DELAY_SECONDS = 1.5  # 每次打證交所 API 之間的間隔,禮貌性延遲


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += datetime.timedelta(days=1)


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("錯誤:缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY 環境變數")
        sys.exit(1)

    start_str = os.environ.get("BACKFILL_START")
    end_str = os.environ.get("BACKFILL_END")
    if not start_str or not end_str:
        print("錯誤:請設定 BACKFILL_START / BACKFILL_END 環境變數,格式 YYYY-MM-DD")
        sys.exit(1)

    start = datetime.date.fromisoformat(start_str)
    end = datetime.date.fromisoformat(end_str)

    symbols = get_active_tw_symbols()
    if not symbols:
        print("watchlist 裡沒有啟用中的台股,請先加入股票")
        return
    print(f"追蹤中的台股: {sorted(symbols)}")
    print(f"回補區間: {start} ~ {end}")

    total_written = 0
    for d in daterange(start, end):
        if d.weekday() >= 5:  # 週六(5)、週日(6)直接跳過,不用浪費請求
            continue

        date_str = d.strftime("%Y%m%d")
        print(f"\n抓取 {date_str} ...")

        t86 = fetch_t86(date_str)
        time.sleep(DELAY_SECONDS)
        mi = fetch_mi_index(date_str)
        time.sleep(DELAY_SECONDS)
        qfiis = fetch_mi_qfiis(date_str)
        time.sleep(DELAY_SECONDS)

        if not t86 and not mi:
            print(f"  {date_str} 無資料(非交易日),跳過")
            continue

        rows = []
        for symbol in symbols:
            inst = t86.get(symbol, {})
            price = mi.get(symbol, {})
            if not inst and not price:
                continue
            rows.append({
                "symbol": symbol,
                "market": "TW",
                "date": d.isoformat(),
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

        if rows:
            upsert_daily_data(rows)
            total_written += len(rows)
        else:
            print(f"  {date_str} 抓到資料但沒有符合 watchlist 的股票")

    print(f"\n回補完成,總共寫入 {total_written} 筆資料")


if __name__ == "__main__":
    main()
