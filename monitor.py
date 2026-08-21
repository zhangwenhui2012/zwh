#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票监控脚本
------------
1. 从 Yahoo Finance 拉取每只股票的价格、昨收、52周最高价
2. 计算：距52周高点的回撤、当日涨跌幅
3. 抓取每只股票近期新闻(消息面, best-effort)
4. 把结果写入 docs/data/quotes.json 供看板网页读取
5. 触发告警规则时，用 Gmail SMTP 发邮件；state.json 记录已发状态，避免刷屏

本地测试：不设置 GMAIL_USER / GMAIL_APP_PASSWORD 环境变量即为 dry-run，
只打印将要发送的告警，不真正发邮件，但仍会写出 quotes.json。
"""

import os
import io
import re
import ssl
import json
import time
import smtplib
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

# ------------------------------------------------------------------ 路径
ROOT        = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
STATE_PATH  = os.path.join(ROOT, "state.json")
OUT_PATH    = os.path.join(ROOT, "docs", "data", "quotes.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# ------------------------------------------------------------------ 工具
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def http_get(url, timeout=25, retries=3, sleep=2.0):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json,text/plain,*/*",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(sleep * (i + 1))
    raise last

# ------------------------------------------------------------------ 行情
def fetch_quote(symbol):
    """返回 dict: price, prev_close, high_52w, currency, name, exchange, day_change_pct, drawdown_pct"""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol)
           + "?range=1y&interval=1d&includePrePost=false")
    raw = http_get(url)
    data = json.loads(raw)
    res = data["chart"]["result"][0]
    meta = res["meta"]

    currency = meta.get("currency") or ""
    name = meta.get("shortName") or meta.get("longName") or symbol
    exchange = meta.get("fullExchangeName") or meta.get("exchangeName") or ""

    # 从日线序列取 收盘价 与 最高价
    highs, closes = [], []
    try:
        q0 = res["indicators"]["quote"][0]
        highs = [h for h in q0.get("high", []) if h is not None]
        closes = [c for c in q0.get("close", []) if c is not None]
    except Exception:
        pass

    # 当前价：优先用实时价，缺失则用最近一根日线收盘
    price = meta.get("regularMarketPrice")
    if price is None and closes:
        price = closes[-1]

    # 昨收：日线序列里“上一个已完成交易日”的收盘价 = 倒数第二根
    # (注意：不能用 meta.chartPreviousClose，它在 range=1y 时是一年前的收盘价)
    prev_close = None
    if len(closes) >= 2:
        prev_close = closes[-2]
    elif meta.get("previousClose"):
        prev_close = meta.get("previousClose")

    # 52周最高：取日内 high 序列 与 meta.fiftyTwoWeekHigh 的最大值
    high_52w = max(highs) if highs else None
    mh = meta.get("fiftyTwoWeekHigh")
    if mh:
        high_52w = max(high_52w, mh) if high_52w else mh

    day_change_pct = None
    if price is not None and prev_close:
        day_change_pct = (price - prev_close) / prev_close * 100.0

    drawdown_pct = None
    if price is not None and high_52w:
        drawdown_pct = (high_52w - price) / high_52w * 100.0

    return {
        "symbol": symbol,
        "price": price,
        "prev_close": prev_close,
        "high_52w": high_52w,
        "currency": currency,
        "yahoo_name": name,
        "exchange": exchange,
        "day_change_pct": day_change_pct,
        "drawdown_pct": drawdown_pct,
    }

# ------------------------------------------------------------------ 新闻(消息面)
def fetch_news(company_name, symbol, limit=4):
    """用 Google News RSS 按公司名搜近期新闻，best-effort，失败返回 []"""
    try:
        q = urllib.parse.quote(company_name + " stock")
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        raw = http_get(url, timeout=20, retries=2)
        root = ET.fromstring(raw)
        items = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            src_el = it.find("{*}source")
            src = (src_el.text if src_el is not None else "") or ""
            if title:
                items.append({"title": title, "link": link, "pub": pub, "source": src})
            if len(items) >= limit:
                break
        return items
    except Exception:
        return []

# ------------------------------------------------------------------ 告警判断
def band_for(drawdown_pct, bands):
    """返回当前回撤所处的最高档位(整数%)，不到最小档返回 0"""
    if drawdown_pct is None:
        return 0
    hit = 0
    for b in sorted(bands):
        if drawdown_pct >= b:
            hit = b
    return hit

def evaluate_alerts(quotes, cfg, state, today):
    """返回本轮需要发送的告警列表；同时就地更新 state"""
    bands = cfg.get("drawdown_bands_pct", [5, 10, 15, 20, 25, 30, 40, 50])
    plunge = float(cfg.get("intraday_plunge_pct", 5) or 0)
    alerts = []

    for q in quotes:
        if q.get("error"):
            continue
        sym = q["symbol"]
        st = state.get(sym, {"last_band": 0, "last_plunge_date": ""})

        # 规则1：回撤分档 —— 首次跌破更深的新档位才发
        cur_band = band_for(q.get("drawdown_pct"), bands)
        if cur_band > st.get("last_band", 0):
            alerts.append({
                "type": "drawdown",
                "symbol": sym, "name": q.get("name", sym),
                "band": cur_band,
                "drawdown_pct": q.get("drawdown_pct"),
                "price": q.get("price"), "currency": q.get("currency"),
                "high_52w": q.get("high_52w"),
                "day_change_pct": q.get("day_change_pct"),
            })
        # 回撤会随价格变化上下移动：把 last_band 同步到当前档，
        # 这样恢复后若再次恶化到该档，会重新提醒。
        st["last_band"] = cur_band

        # 规则2：今日大跌 —— 单日跌幅达阈值，每天每股最多一封
        dc = q.get("day_change_pct")
        if plunge > 0 and dc is not None and dc <= -plunge:
            if st.get("last_plunge_date") != today:
                alerts.append({
                    "type": "plunge",
                    "symbol": sym, "name": q.get("name", sym),
                    "day_change_pct": dc,
                    "price": q.get("price"), "currency": q.get("currency"),
                    "drawdown_pct": q.get("drawdown_pct"),
                })
                st["last_plunge_date"] = today

        state[sym] = st

    return alerts

# ------------------------------------------------------------------ 邮件
def fmt_pct(x, signed=False):
    if x is None:
        return "—"
    return (f"{x:+.2f}%" if signed else f"{x:.2f}%")

def fmt_price(p, cur):
    if p is None:
        return "—"
    return f"{p:,.2f} {cur}".strip()

def build_email_html(alerts, quotes, now_str):
    rows = []
    for a in alerts:
        if a["type"] == "plunge":
            headline = f'今日大跌 {fmt_pct(a["day_change_pct"], True)}'
            color = "#c0392b"
        else:
            headline = f'跌破 52周高点 -{a["band"]}%（当前回撤 {fmt_pct(a["drawdown_pct"])}）'
            color = "#d35400"
        rows.append(f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;font-weight:700;">{a['name']}<br>
              <span style="color:#888;font-weight:400;font-size:12px;">{a['symbol']}</span></td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;color:{color};font-weight:700;">{headline}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;">{fmt_price(a.get('price'), a.get('currency',''))}<br>
              <span style="color:#888;font-size:12px;">今日 {fmt_pct(a.get('day_change_pct'), True)}</span></td>
        </tr>""")
    table = "".join(rows)
    return f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:640px;margin:0 auto;">
      <h2 style="margin:0 0 4px;">📉 股票告警</h2>
      <p style="color:#888;margin:0 0 16px;font-size:13px;">{now_str}</p>
      <table style="border-collapse:collapse;width:100%;font-size:14px;">
        <thead><tr style="text-align:left;color:#666;font-size:12px;">
          <th style="padding:6px 12px;">股票</th><th style="padding:6px 12px;">触发原因</th>
          <th style="padding:6px 12px;text-align:right;">现价</th></tr></thead>
        <tbody>{table}</tbody>
      </table>
      <p style="color:#aaa;font-size:12px;margin-top:20px;">此邮件由你的股票监控系统自动发送。</p>
    </div>"""

def send_email(subject, html, cfg):
    user = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    to = (cfg.get("alert_to") or user).strip()

    if not user or not pw:
        print("[dry-run] 未配置 GMAIL_USER / GMAIL_APP_PASSWORD，跳过真正发信。")
        print("[dry-run] 邮件主题：", subject)
        return False

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("股票监控", user))
    msg["To"] = to

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as s:
        s.login(user, pw)
        s.sendmail(user, [to], msg.as_string())
    print(f"[email] 已发送告警邮件到 {to}")
    return True

# ------------------------------------------------------------------ 主流程
def main():
    cfg = load_json(CONFIG_PATH, {})
    tickers = cfg.get("tickers", [])
    state = load_json(STATE_PATH, {})

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    quotes = []
    for t in tickers:
        sym = t["symbol"]
        disp = t.get("name", sym)
        try:
            q = fetch_quote(sym)
            q["name"] = disp
            q["news"] = fetch_news(disp, sym)
            print(f"OK  {sym:12} {fmt_price(q['price'], q['currency'])}  "
                  f"日 {fmt_pct(q['day_change_pct'], True):>8}  "
                  f"距52周高 -{fmt_pct(q['drawdown_pct'])}")
        except Exception as e:
            print(f"ERR {sym:12} {e}")
            q = {"symbol": sym, "name": disp, "error": str(e), "news": []}
        quotes.append(q)
        time.sleep(0.8)  # 温和限速，降低被限流概率

    # 告警
    alerts = evaluate_alerts(quotes, cfg, state, today)

    # 写看板数据
    payload = {
        "updated_at": now.isoformat(),
        "updated_at_display": now_str,
        "quotes": quotes,
        "config": {
            "drawdown_bands_pct": cfg.get("drawdown_bands_pct"),
            "intraday_plunge_pct": cfg.get("intraday_plunge_pct"),
        },
    }
    save_json(OUT_PATH, payload)
    print(f"[data] 已写入 {os.path.relpath(OUT_PATH, ROOT)}")

    # 发邮件
    if alerts:
        n = len(alerts)
        heads = []
        for a in alerts[:3]:
            if a["type"] == "plunge":
                heads.append(f'{a["symbol"]} {fmt_pct(a["day_change_pct"], True)}')
            else:
                heads.append(f'{a["symbol"]} 破-{a["band"]}%')
        subject = "⚠️ 股票告警：" + "，".join(heads) + (f" 等{n}项" if n > 3 else "")
        html = build_email_html(alerts, quotes, now_str)
        try:
            send_email(subject, html, cfg)
        except Exception as e:
            print(f"[email] 发送失败：{e}")
    else:
        print("[alert] 本轮无新增告警。")

    # 保存状态(去重用)
    save_json(STATE_PATH, state)
    print("[state] 已保存 state.json")


if __name__ == "__main__":
    main()
