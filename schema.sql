-- 在 Supabase 的 SQL Editor 貼上執行一次即可

-- 自選股清單
create table if not exists watchlist (
  id bigint generated always as identity primary key,
  symbol text not null,              -- 例如 '2330'、'AAPL'
  market text not null check (market in ('TW', 'US')),
  name text,                          -- 股票名稱,方便顯示
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (symbol, market)
);

-- 每日資料(台股含三大法人,美股先只有價格欄位會用到)
create table if not exists daily_data (
  id bigint generated always as identity primary key,
  symbol text not null,
  market text not null default 'TW',
  date date not null,
  close_price numeric,
  change_percent numeric,
  volume bigint,
  foreign_net bigint,                 -- 外資買賣超股數(僅台股)
  trust_net bigint,                   -- 投信買賣超股數(僅台股)
  dealer_net bigint,                  -- 自營商買賣超股數(僅台股)
  institutional_net bigint,           -- 三大法人合計買賣超股數(僅台股)
  created_at timestamptz not null default now(),
  unique (symbol, market, date)
);

create index if not exists idx_daily_data_symbol_date
  on daily_data (symbol, date desc);

-- Row Level Security:dashboard 前端只用 anon key 讀取,不能寫入/刪除
alter table watchlist enable row level security;
alter table daily_data enable row level security;

create policy "anon can read watchlist"
  on watchlist for select
  to anon
  using (true);

create policy "anon can read daily_data"
  on daily_data for select
  to anon
  using (true);

-- 寫入只透過 service_role key(GitHub Actions 用),不受上面政策限制,
-- 因為 service_role 會繞過 RLS,所以不需要額外幫它開 policy。

-- 範例:先加入幾檔你想追蹤的台股(自行修改)
-- insert into watchlist (symbol, market, name) values
--   ('2330', 'TW', '台積電'),
--   ('2317', 'TW', '鴻海'),
--   ('2454', 'TW', '聯發科')
-- on conflict (symbol, market) do nothing;
