"""
抓取真实行情与估值快照，落盘到 fixtures/source/

所有数字均来自公开接口，不做任何人工填写：
- 行情：新浪（ak.stock_zh_a_spot）/ 腾讯（ak.stock_zh_a_hist_tx）
- 估值：百度股市通（ak.stock_zh_valuation_baidu，PE-TTM / PB）

注意：本机 IE 代理指向 127.0.0.1:7890 但服务未启动，requests 会 ProxyError，
因此导入 akshare 前必须先设 NO_PROXY=*，否则所有东财/百度接口都会失败。
东财接口（push2）在当前网络下被限流，故不使用。

用法：
    python scripts/fetch_source.py --out fixtures/source
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

# 必须在 import akshare 之前设置，否则 requests 会读到失效的 IE 代理
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402

# 三个行业各 5 只，行业归属是公开分类常识，不是抓取所得
POOL = {
    "酿酒行业": [
        ("600519", "贵州茅台"),
        ("000858", "五粮液"),
        ("000568", "泸州老窖"),
        ("600809", "山西汾酒"),
        ("002304", "洋河股份"),
    ],
    "银行": [
        ("600036", "招商银行"),
        ("601398", "工商银行"),
        ("601939", "建设银行"),
        ("601288", "农业银行"),
        ("601166", "兴业银行"),
    ],
    "电池": [
        ("300750", "宁德时代"),
        ("002594", "比亚迪"),
        ("300014", "亿纬锂能"),
        ("002460", "赣锋锂业"),
        ("300207", "欣旺达"),
    ],
}

# 日报主体：每行业挑 1-2 只，共 5 只
SUBJECT = ["600519", "000858", "600036", "300750", "300014"]

HIST_DAYS = 30


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return "sha256:" + h.hexdigest()


def sina_prefix(code: str) -> str:
    return ("sh" if code.startswith(("6", "5")) else "sz") + code


def fetch_spot() -> pd.DataFrame:
    """新浪全市场快照：只有价格与成交，没有估值"""
    for attempt in range(3):
        try:
            return ak.stock_zh_a_spot()
        except Exception as exc:  # 网络抖动时重试
            if attempt == 2:
                raise
            print(f"  spot 第 {attempt + 1} 次失败：{type(exc).__name__}，重试", file=sys.stderr)
    raise RuntimeError("unreachable")


def fetch_valuation(code: str, indicator: str) -> tuple[float, str]:
    """百度估值：返回（最新值，该值所属日期）"""
    df = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period="近一年")
    if df is None or df.empty:
        raise ValueError(f"{code} {indicator} 无数据")
    last = df.iloc[-1]
    return float(last["value"]), str(last["date"])


def fetch_history(code: str, days: int = HIST_DAYS) -> pd.DataFrame:
    """腾讯日线，取最近 N 个交易日"""
    df = ak.stock_zh_a_hist_tx(symbol=sina_prefix(code), adjust="")
    df = df.tail(days).copy()
    df["code"] = code
    return df[["code", "date", "open", "close", "high", "low", "amount"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fixtures/source")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("[1/3] 拉取新浪全市场快照 ...", file=sys.stderr)
    spot = fetch_spot()
    spot_idx = {str(r["代码"]).replace("sh", "").replace("sz", ""): r for _, r in spot.iterrows()}

    print("[2/3] 拉取估值（百度 PE-TTM / PB）...", file=sys.stderr)
    rows = []
    for industry, members in POOL.items():
        for code, name in members:
            pe, pe_date = fetch_valuation(code, "市盈率(TTM)")
            pb, pb_date = fetch_valuation(code, "市净率")
            s = spot_idx.get(code)
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "industry": industry,
                    "last_price": float(s["最新价"]) if s is not None else None,
                    "change_pct": float(s["涨跌幅"]) if s is not None else None,
                    "turnover": float(s["成交额"]) if s is not None else None,
                    "pe_ttm": pe,
                    "pe_date": pe_date,
                    "pb": pb,
                    "pb_date": pb_date,
                    "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
            print(f"  {code} {name} PE={pe} ({pe_date}) PB={pb} ({pb_date})", file=sys.stderr)

    stocks = pd.DataFrame(rows)
    stocks.to_csv(out / "stocks.csv", index=False, encoding="utf-8-sig")

    print("[3/3] 拉取主体近 30 日行情 ...", file=sys.stderr)
    hist = pd.concat([fetch_history(c) for c in SUBJECT], ignore_index=True)
    hist.to_csv(out / "prices_30d.csv", index=False, encoding="utf-8-sig")

    # 行业均值：用同行业 5 只的中位数，避免被单只极值带偏
    avg_rows = []
    for industry, grp in stocks.groupby("industry"):
        avg_rows.append(
            {
                "industry": industry,
                "n_samples": int(len(grp)),
                "pe_median": round(statistics.median(grp["pe_ttm"]), 4),
                "pb_median": round(statistics.median(grp["pb"]), 4),
                "method": "median",
            }
        )
    pd.DataFrame(avg_rows).to_csv(out / "industry_avg.csv", index=False, encoding="utf-8-sig")

    # 30 日均价，供"价格 vs 均线"类 claim 回源
    ma_rows = []
    for code, grp in hist.groupby("code"):
        grp = grp.sort_values("date")
        ma_rows.append(
            {
                "code": code,
                "n_days": int(len(grp)),
                "first_date": str(grp.iloc[0]["date"]),
                "last_date": str(grp.iloc[-1]["date"]),
                "last_close": float(grp.iloc[-1]["close"]),
                "ma30": round(float(grp["close"].mean()), 4),
            }
        )
    pd.DataFrame(ma_rows).to_csv(out / "ma30.csv", index=False, encoding="utf-8-sig")

    meta = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "quotes": "akshare.stock_zh_a_spot (新浪)",
            "valuation": "akshare.stock_zh_valuation_baidu (百度股市通)",
            "history": "akshare.stock_zh_a_hist_tx (腾讯)",
        },
        "excluded": "东财 push2 系列在本次网络下被限流，未使用",
        "industry_method": "同行业 5 只样本的 PE(TTM)/PB 中位数",
        "files": {},
    }
    for name in ["stocks.csv", "prices_30d.csv", "industry_avg.csv", "ma30.csv"]:
        meta["files"][name] = {
            "sha256": sha256_file(out / name),
            "rows": int(sum(1 for _ in (out / name).open(encoding="utf-8-sig")) - 1),
        }
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"完成：{out}（{len(stocks)} 只标的 / {len(hist)} 条日线）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
