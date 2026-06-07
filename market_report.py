# -*- coding: utf-8 -*-
"""
일일 시장 리포트 생성기 (국내 코스피 / 미국 나스닥)
- 등락률: 전일(직전 영업일) 대비
- 거래량/시총 상위 3: 가장 최근 영업일 기준, 현재가·등락률 포함
- 교차 검증: 핵심 수치(코스피/나스닥/환율)를 yfinance ↔ 네이버 두 소스에서 대조
  → 오차 초과 시 ⚠️ 표시 후 그대로 게시 (게시 중단 없음)
- 결과물: index.html  (GitHub Pages가 그대로 게시)

실행: python market_report.py
※ 2025-12-27부터 KRX 정보데이터시스템이 로그인 필수로 바뀌어 pykrx 대신
   yfinance(^KS11) + 네이버 금융 공개 API를 사용합니다.
※ 데이터 소스는 외부 서버라 항목별로 try/except로 감쌌습니다.
   한 곳이 실패해도 나머지 리포트는 정상 생성됩니다.
"""

import csv
import datetime
import io
import json
import re

import requests
import yfinance as yf

NAVER = requests.Session()
NAVER.trust_env = False          # 로컬 .netrc 간섭 회피
NAVER.headers.update({"User-Agent": "Mozilla/5.0"})


def naver_json(url):
    r = NAVER.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def num(s):
    """네이버 API의 '8,160.59' 같은 문자열 숫자 → float."""
    return float(str(s).replace(",", ""))


# ──────────────────────────────────────────────
# yfinance 공통
# ──────────────────────────────────────────────
def yf_close(ticker, period="7d"):
    h = yf.Ticker(ticker).history(period=period)
    return h["Close"].dropna()


def prev_change(close):
    """종가 시리즈의 마지막 2개 영업일로 (최종가, 전일 대비 %) 계산."""
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    return last, (last / prev - 1) * 100


def yf_last(ticker):
    return float(yf_close(ticker).iloc[-1])


def yf_series_month(ticker):
    """차트용: 최근 1개월 (날짜 리스트, 종가 리스트)."""
    close = yf_close(ticker, period="1mo")
    dates = [d.strftime("%Y-%m-%d") for d in close.index]
    return dates, [float(v) for v in close]


# ──────────────────────────────────────────────
# 국내장 (코스피) — 네이버 금융 공개 API
# ──────────────────────────────────────────────
def kr_top_cap():
    """시총 상위 3 (KOSPI): [{name, price, pct}]."""
    j = naver_json("https://m.stock.naver.com/api/stocks/marketValue/KOSPI?page=1&pageSize=3")
    return [{"name": s["stockName"], "price": num(s["closePrice"]),
             "pct": num(s["fluctuationsRatio"])} for s in j["stocks"]]


def kr_top_volume():
    """거래량 상위 3 (KOSPI, ETF/ETN/스팩 제외 일반주만): [{name, price, pct}]."""
    r = NAVER.get("https://finance.naver.com/sise/sise_quant.naver?sosok=0", timeout=15)
    r.raise_for_status()
    r.encoding = "euc-kr"
    codes = re.findall(r'<a href="/item/main\.naver\?code=(\d{6})"[^>]*>[^<]+</a>', r.text)

    picked = []
    for code in codes[:20]:                      # 상위 20개 안에서 일반주 3개 탐색
        try:
            j = naver_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
        except Exception:
            continue
        if j.get("stockEndType") != "stock":     # ETF/ETN 등 제외
            continue
        picked.append({"name": j["stockName"], "price": num(j["closePrice"]),
                       "pct": num(j["fluctuationsRatio"])})
        if len(picked) == 3:
            break
    return picked


def naver_stock(code):
    """네이버 종목 기본정보: {name, price, pct}. 예: 498400(KODEX 200타겟위클리커버드콜)."""
    j = naver_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
    return {"name": j["stockName"], "price": num(j["closePrice"]),
            "pct": num(j["fluctuationsRatio"])}


def kr_rate(marketindex_cd):
    """네이버 시장지표 금리(%) 최신값. 예: IRR_GOVT03Y(국고채 3년), IRR_CD91(CD 91일).
    ※ 국고채 1년/10년물은 네이버 미제공 — 단기금리는 CD 91일물로 대체."""
    r = NAVER.get("https://finance.naver.com/marketindex/interestDailyQuote.naver"
                  f"?marketindexCd={marketindex_cd}&page=1", timeout=15)
    r.raise_for_status()
    r.encoding = "euc-kr"
    nums = re.findall(r'<td class="num">([\d.]+)</td>', r.text)
    return float(nums[0]) if nums else None


# ──────────────────────────────────────────────
# 미국장 (나스닥)
# ──────────────────────────────────────────────
# 시총 상위는 거의 고정이므로 메가캡 유니버스에서 산출. (전부 나스닥 상장)
US_UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO"]


def us_top():
    rows = []
    for t in US_UNIVERSE:
        try:
            info = yf.Ticker(t).fast_info
            last = float(getattr(info, "last_price", 0) or 0)
            prev = float(getattr(info, "previous_close", 0) or 0)
            rows.append({"name": t,
                         "cap": float(getattr(info, "market_cap", 0) or 0),
                         "vol": float(getattr(info, "last_volume", 0) or 0),
                         "price": last,
                         "pct": (last / prev - 1) * 100 if prev else 0.0})
        except Exception as e:
            print(t, "조회 실패:", e)
    top_vol = sorted(rows, key=lambda r: r["vol"], reverse=True)[:3]
    top_cap = sorted(rows, key=lambda r: r["cap"], reverse=True)[:3]
    return top_vol, top_cap


# ──────────────────────────────────────────────
# 보유종목 목표 매도가 (사용자 Google Sheet — 읽기 전용)
# 규칙: A열 텍스트 = 종목 블록 시작 / 2026년 이후 거래만 /
#       매도예정가(I열) 빈칸 무시 / 같은 인격이 이후 매도했으면 그 lot 제외
# ──────────────────────────────────────────────
TARGET_SHEET_ID = "1qj-FAIVW9Umdlg61675nJJ9PNilZ61qvYwb5TBDQtNk"
TARGET_SHEET_GID = "0"
TARGET_SINCE = datetime.date(2026, 1, 1)

# 종목명 → (시세조회 키, 통화). 'us'/'btc'=yfinance, 'kr'=네이버 종목코드
TICKER_MAP = {
    "TQQQ": ("TQQQ", "us"),
    "비트코인": ("BTC-KRW", "btc"),
    "KODEX코스닥150": ("229200", "kr"),
    "코스닥150": ("229200", "kr"),
    "KODEX코스닥150레버리지": ("233740", "kr"),
    "코스닥150레버리지": ("233740", "kr"),
    "맥쿼리인프라": ("088980", "kr"),
    "동서": ("026960", "kr"),
    "KODEX200타겟위클리커버드콜": ("498400", "kr"),
}


def _parse_date(s):
    s = str(s).strip().replace(".", "-").replace("/", "-")
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def resolve_ticker(name):
    """종목명 → (키, 통화구분). 미등록이면 네이버 자동완성으로 탐색, 실패 시 None."""
    key = re.sub(r"\s+", "", name).upper()
    m = re.search(r"\((\d{6})\)", name)
    if m:
        return m.group(1), "kr"
    for k, v in TICKER_MAP.items():
        if k.upper() == key:
            return v
    try:
        import urllib.parse
        q = urllib.parse.quote(name)
        j = naver_json(f"https://ac.stock.naver.com/ac?q={q}&target=stock")
        items = j.get("items") or []
        if items and items[0].get("nationCode") == "KOR":
            return items[0]["code"], "kr"
    except Exception as e:
        print("종목 탐색 실패:", name, e)
    return None


def fetch_targets():
    """시트에서 목표 lot 추출: {종목명: {"sell": [{qty, target}], "buy": [{qty, target}]}}.
    sell = 2026+ 매수 행 중 매도예정가(I열) 있는 미체결 lot
    buy  = B열이 '목표매수가'인 행 (C열=가격, D열=수량)"""
    url = (f"https://docs.google.com/spreadsheets/d/{TARGET_SHEET_ID}"
           f"/export?format=csv&gid={TARGET_SHEET_GID}")
    r = NAVER.get(url, timeout=20)
    r.raise_for_status()
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8"))))

    blocks = {}          # 종목명 → [(행번호, 인격, 수량, 목표가)]
    sells = {}           # 종목명 → [(행번호, 인격)]
    buys = {}            # 종목명 → [{qty, target}]
    current = None
    HEADER_LABELS = {"월", "연월", "년도", "일자"}
    for i, row in enumerate(rows):
        a = (row[0].strip() if row else "")
        b = (row[1].strip() if len(row) > 1 else "")
        if a and not a[0].isdigit():
            # 거래 표의 컬럼 헤더 행("월,일자,…")은 마커가 아님 — 블록 유지
            if a in HEADER_LABELS or b in ("일자", "날짜"):
                continue
            current = a                          # 종목 블록 마커
            continue
        if current is None:
            continue
        if "목표매수가" in b:                     # 목표 매수가 행: C=가격, D=수량
            try:
                price = num(row[2].replace("₩", "").replace("$", ""))
                qty = abs(num(row[3]))
                buys.setdefault(current, []).append({"qty": qty, "target": price})
            except Exception as e:
                print("목표매수가 행 파싱 실패:", current, e)
            continue
        if len(row) < 9:
            continue
        d = _parse_date(row[1] if len(row) > 1 else "")
        if d is None or d < TARGET_SINCE:
            continue
        trade = (row[5] if len(row) > 5 else "").strip()
        persona = (row[4] if len(row) > 4 else "").strip()
        if "매도" in trade and persona:
            sells.setdefault(current, []).append((i, persona))
        if "매수" not in trade:
            continue
        sell_target = (row[8] if len(row) > 8 else "").strip()
        if not sell_target:
            continue
        try:
            qty = abs(num(row[3]))
            target = num(sell_target.replace("₩", "").replace("$", ""))
        except Exception:
            continue
        blocks.setdefault(current, []).append((i, persona, qty, target))

    # 같은 인격이 lot 이후에 매도했으면 제외 (이미 체결된 자리)
    out = {}
    for name, lots in blocks.items():
        keep = []
        for (i, persona, qty, target) in lots:
            sold_later = persona and any(
                si > i and sp == persona for si, sp in sells.get(name, []))
            if not sold_later:
                keep.append({"qty": qty, "target": target})
        if keep:
            out.setdefault(name, {"sell": [], "buy": []})["sell"] = \
                sorted(keep, key=lambda x: x["target"])
    for name, lots in buys.items():
        out.setdefault(name, {"sell": [], "buy": []})["buy"] = \
            sorted(lots, key=lambda x: x["target"], reverse=True)
    return out


def fetch_target_prices(targets):
    """목표 추적 종목들의 (전일 종가, 등락률, 통화) 조회: {종목명: (last, pct, cur)}."""
    prices = {}
    for name in targets:
        rt = resolve_ticker(name)
        if not rt:
            continue
        key, kind = rt
        try:
            if kind == "kr":
                s = naver_stock(key)
                prices[name] = (s["price"], s["pct"], "₩")
            else:
                last, pct = prev_change(yf_close(key))
                prices[name] = (last, pct, "$" if kind == "us" else "₩")
        except Exception as e:
            print("목표종목 시세 실패:", name, e)
    return prices


# ──────────────────────────────────────────────
# 교차 검증 (yfinance ↔ 네이버, 같은 날짜끼리 대조)
# ──────────────────────────────────────────────
def naver_index_last(code, world=False):
    """네이버 지수/환율의 (값, 'YYYY-MM-DD'). 실패 시 None."""
    try:
        if world:
            j = naver_json(f"https://api.stock.naver.com/index/{code}/basic")
        else:
            j = naver_json(f"https://m.stock.naver.com/api/index/{code}/basic")
        return num(j["closePrice"]), str(j["localTradedAt"])[:10]
    except Exception as e:
        print("네이버 지수 조회 실패:", code, e)
        return None


def naver_fx_last():
    try:
        j = naver_json("https://m.stock.naver.com/front-api/marketIndex/prices"
                       "?category=exchange&reutersCode=FX_USDKRW&page=1&pageSize=10")
        first = j["result"][0]
        return num(first["closePrice"]), str(first["localTradedAt"])[:10]
    except Exception as e:
        print("네이버 환율 조회 실패:", e)
        return None


def cross_check(label, yf_series, secondary, tol_pct, match_date=True):
    """
    yf_series: yfinance 종가 시리즈(날짜 인덱스), secondary: (값, 날짜) 또는 None.
    match_date=True : 2차 소스의 날짜에 해당하는 yfinance 값과 비교 (지수 — 시점 차이 오탐 방지)
    match_date=False: 양쪽 최신값끼리 비교 (환율 — 24시간 거래라 소스별 날짜 표기가 달라 날짜 매칭이 오히려 오탐)
    반환: {"label", "status": ok|warn|na, "diff_pct", "p", "s"}
    """
    if yf_series is None or len(yf_series) == 0 or not secondary:
        return {"label": label, "status": "na", "diff_pct": None, "p": None,
                "s": secondary[0] if secondary else None}
    sv, sd = secondary
    if match_date:
        by_date = {d.strftime("%Y-%m-%d"): float(v) for d, v in yf_series.items()}
        pv = by_date.get(sd)
        if pv is None:                              # 날짜 불일치 → 검증 불가
            return {"label": label, "status": "na", "diff_pct": None,
                    "p": float(yf_series.iloc[-1]), "s": sv}
    else:
        pv = float(yf_series.iloc[-1])
    diff = abs(pv / sv - 1) * 100 if sv else 999.0
    return {"label": label, "status": "ok" if diff <= tol_pct else "warn",
            "diff_pct": diff, "p": pv, "s": sv}


def run_validations(series_map):
    checks = []
    checks.append(cross_check("KOSPI", series_map.get("^KS11"),
                              naver_index_last("KOSPI"), 0.5))
    checks.append(cross_check("나스닥", series_map.get("^IXIC"),
                              naver_index_last(".IXIC", world=True), 0.5))
    checks.append(cross_check("원/달러", series_map.get("KRW=X"),
                              naver_fx_last(), 1.0, match_date=False))
    return checks


# ──────────────────────────────────────────────
# 차트 데이터 (최근 1개월, 기준일=100 정규화)
# ──────────────────────────────────────────────
def build_chart_payload():
    datasets, all_dates = [], set()

    def add(ticker, label, color):
        try:
            dates, closes = yf_series_month(ticker)
        except Exception as e:
            print(label, "차트 실패:", e)
            return
        if not closes:
            return
        base = closes[0]
        all_dates.update(dates)
        pts = [{"x": d, "y": round(v / base * 100, 2)} for d, v in zip(dates, closes)]
        datasets.append({"label": label, "data": pts, "borderColor": color,
                         "backgroundColor": color, "tension": 0.2,
                         "pointRadius": 0, "borderWidth": 2, "spanGaps": True})

    add("^KS11", "KOSPI", "#d23f3f")
    add("^IXIC", "나스닥", "#2c5fd0")

    # 한·미 거래일 캘린더가 달라 합집합 날짜를 공통 x축 라벨로 사용
    return {"labels": sorted(all_dates), "datasets": datasets}


# ──────────────────────────────────────────────
# HTML 렌더링
# ──────────────────────────────────────────────
def li(label, value, warn=False, tooltip=""):
    mark = f'<span class="warn" title="{tooltip}">⚠️</span> ' if warn else ""
    return f'<li><span class="lbl">{label}</span><span class="val">{mark}{value}</span></li>'


def note_li(text):
    """지표 설명용 보조 텍스트 줄."""
    return f'<li class="noteline"><span class="note">{text}</span></li>'


def sign(pct):
    arrow = "▲" if pct >= 0 else "▼"
    cls = "up" if pct >= 0 else "down"
    return f'<span class="{cls}">{arrow} {abs(pct):.2f}%</span>'


def stocks_html(rows, krw=True):
    """상위 종목: 종목명 + 현재가 + 등락률 (한 줄씩)."""
    out = []
    for r in rows:
        price = f"{r['price']:,.0f}" if krw else f"${r['price']:,.2f}"
        out.append(f'<div class="stk"><span>{r["name"]}</span>'
                   f'<span>{price} {sign(r["pct"])}</span></div>')
    return "".join(out)


def fmt_money(v, cur):
    return f"${v:,.2f}" if cur == "$" else f"{v:,.0f}원"


def targets_card(targets, prices):
    """보유종목 목표 매도/매수가 카드 HTML과 도달 배너 HTML 반환."""
    if not targets:
        return "", ""
    stocks_html_parts, hits = [], []
    for name, sides in targets.items():
        p = prices.get(name)
        if p:
            last, pct, cur = p
            head_val = f"전일 종가 {fmt_money(last, cur)} {sign(pct)}"
        else:
            last, cur = None, "₩"
            head_val = "시세 조회 실패"
        unit = " BTC" if "비트코인" in name else "주"
        rows = []

        def lot_row(lot, side):
            qty_txt = f"{lot['qty']:,.4f}".rstrip("0").rstrip(".") if lot["qty"] < 1 \
                else f"{lot['qty']:,.0f}"
            if side == "sell":
                left = f"{qty_txt}{unit} → {fmt_money(lot['target'], cur)} 이상 매도"
            else:
                left = f"{qty_txt}{unit} → {fmt_money(lot['target'], cur)} 이하 매수"
            if last is not None:
                if side == "sell":
                    rate, hit = last / lot["target"] * 100, last >= lot["target"]
                else:
                    rate, hit = lot["target"] / last * 100, last <= lot["target"]
                right = f"달성률 {rate:.1f}%" + (" ✅ 도달" if hit else "")
                if hit:
                    word = "매도" if side == "sell" else "매수"
                    hits.append(f"{name} {word} {fmt_money(lot['target'], cur)} ({qty_txt}{unit})")
            else:
                right, hit = "—", False
            cls = "tgt-row" + (" buy" if side == "buy" else "") + (" hit" if hit else "")
            return (f'<div class="{cls}">'
                    f'<span>{left}</span><span>{right}</span></div>')

        for lot in sides.get("sell", []):
            rows.append(lot_row(lot, "sell"))
        for lot in sides.get("buy", []):
            rows.append(lot_row(lot, "buy"))
        stocks_html_parts.append(
            f'<div class="tgt-stock"><div class="tgt-head"><span>{name}</span>'
            f'<span>{head_val}</span></div>{"".join(rows)}</div>')
    card = (f'<div class="card chart-card"><h2>🎯 보유종목 목표 매도·매수가</h2>'
            f'{"".join(stocks_html_parts)}</div>')
    banner = (f'<div class="vbanner vhit">🎯 목표가 도달: {" · ".join(hits)}</div>'
              if hits else "")
    return card, banner


def validation_banner(checks):
    warns = [c for c in checks if c["status"] == "warn"]
    oks = [c for c in checks if c["status"] == "ok"]
    nas = [c for c in checks if c["status"] == "na"]
    if warns:
        detail = ", ".join(f'{c["label"]} 오차 {c["diff_pct"]:.2f}%' for c in warns)
        return f'<div class="vbanner vwarn">⚠️ 교차검증 주의 {len(warns)}건 — {detail}</div>'
    txt = f"✅ 데이터 교차검증 통과 ({len(oks)}건"
    txt += f", 검증불가 {len(nas)}건)" if nas else ")"
    return f'<div class="vbanner vok">{txt}</div>'


def warn_args(checks, label):
    for c in checks:
        if c["label"] == label and c["status"] == "warn":
            return True, (f'yfinance {c["p"]:,.2f} / 네이버 {c["s"]:,.2f} '
                          f'(오차 {c["diff_pct"]:.2f}%)')
    return False, ""


def build_html():
    parts_kr, parts_us = [], []

    # 핵심 시리즈는 한 번만 받아 등락률·교차검증에 재사용
    series_map = {}
    for tk in ["^KS11", "^IXIC", "KRW=X", "^VIX"]:
        try:
            series_map[tk] = yf_close(tk)
        except Exception as e:
            print(tk, "조회 실패:", e)
            series_map[tk] = None

    checks = run_validations(series_map)

    kr_date = ""
    if series_map.get("^KS11") is not None and len(series_map["^KS11"]):
        kr_date = series_map["^KS11"].index[-1].strftime("%Y-%m-%d")

    # ── 국내 코스피시장 ──
    try:
        last, pct = prev_change(series_map["^KS11"])
        w, tip = warn_args(checks, "KOSPI")
        parts_kr.append(li("KOSPI", f"{last:,.2f} {sign(pct)}", w, tip))
    except Exception as e:
        parts_kr.append(li("KOSPI", f"조회 실패 ({e})"))
    try:
        b = kr_rate("IRR_GOVT03Y")
        if b is not None:
            parts_kr.append(li("국고채 3년", f"{b:.2f}%"))
    except Exception as e:
        print("국고채 실패:", e)
    try:
        cd = kr_rate("IRR_CD91")
        if cd is not None:
            parts_kr.append(li("단기금리 CD(91일)", f"{cd:.2f}%"))
    except Exception as e:
        print("CD금리 실패:", e)
    try:
        last_fx, fx_pct = prev_change(series_map["KRW=X"])
        w, tip = warn_args(checks, "원/달러")
        parts_kr.append(li("원/달러 환율", f"{last_fx:,.1f} {sign(fx_pct)}", w, tip))
    except Exception as e:
        print("환율 실패:", e)
    try:
        parts_kr.append(li("거래량 상위 3", stocks_html(kr_top_volume())))
    except Exception as e:
        parts_kr.append(li("거래량 상위 3", f"조회 실패 ({e})"))
    try:
        parts_kr.append(li("시총 상위 3", stocks_html(kr_top_cap())))
    except Exception as e:
        parts_kr.append(li("시총 상위 3", f"조회 실패 ({e})"))
    try:
        k = naver_stock("498400")            # KODEX 200타겟위클리커버드콜
        parts_kr.append(li("KODEX 200타겟위클리커버드콜",
                           f"{k['price']:,.0f} {sign(k['pct'])}"))
    except Exception as e:
        print("KODEX 커버드콜 실패:", e)

    # ── 미국 나스닥시장 ──
    try:
        last, pct = prev_change(series_map["^IXIC"])
        w, tip = warn_args(checks, "나스닥")
        parts_us.append(li("나스닥 종합", f"{last:,.2f} {sign(pct)}", w, tip))
    except Exception as e:
        parts_us.append(li("나스닥 종합", f"조회 실패 ({e})"))
    try:
        vix_last, vix_pct = prev_change(series_map["^VIX"])
        parts_us.append(li("공포지수(VIX)", f"{vix_last:.2f} {sign(vix_pct)}"))
        parts_us.append(note_li(
            "VIX는 S&P500 옵션 가격으로 산출한 향후 30일 예상 변동성으로, "
            "투자자 불안 심리를 나타냅니다. 통상 20 미만이면 안정, 30 이상이면 공포 구간으로 봅니다."))
    except Exception as e:
        print("VIX 실패:", e)
    try:
        parts_us.append(li("미 국채 3개월", f"{yf_last('^IRX'):.2f}%"))
    except Exception as e:
        print("미 국채 3개월 실패:", e)
    try:
        parts_us.append(li("미 국채 10년", f"{yf_last('^TNX'):.2f}%"))
    except Exception as e:
        print("미 국채 실패:", e)
    try:
        parts_us.append(li("달러인덱스(DXY)", f"{yf_last('DX-Y.NYB'):.2f}"))
    except Exception as e:
        print("달러인덱스 실패:", e)
    try:
        tv, tc = us_top()
        parts_us.append(li("거래량 상위 3", stocks_html(tv, krw=False)))
        parts_us.append(li("시총 상위 3", stocks_html(tc, krw=False)))
    except Exception as e:
        parts_us.append(li("상위 종목", f"조회 실패 ({e})"))
    try:
        tqqq_last, tqqq_pct = prev_change(yf_close("TQQQ"))
        parts_us.append(li("TQQQ (나스닥100 3배)", f"${tqqq_last:,.2f} {sign(tqqq_pct)}"))
    except Exception as e:
        print("TQQQ 실패:", e)

    # ── 보유종목 목표 매도가 (시트 읽기 실패 시 섹션 생략) ──
    try:
        targets = fetch_targets()
        tgt_card, tgt_banner = targets_card(targets, fetch_target_prices(targets))
    except Exception as e:
        print("목표가 시트 조회 실패:", e)
        tgt_card, tgt_banner = "", ""

    chart_json = json.dumps(build_chart_payload(), ensure_ascii=False)
    banner = validation_banner(checks)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>일일 시장 리포트</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
 body{{font-family:'Pretendard',system-ui,sans-serif;background:#f5f6f8;color:#1a1a2e;margin:0;padding:32px}}
 h1{{font-size:22px;margin:0 0 4px}} .stamp{{color:#888;font-size:13px;margin-bottom:10px}}
 .vbanner{{display:inline-block;font-size:13px;padding:6px 12px;border-radius:8px;margin-bottom:20px}}
 .vok{{background:#e8f5ec;color:#1d7a3d}} .vwarn{{background:#fdf0e0;color:#a05c00}}
 .vhit{{background:#fff3cd;color:#8a6100;font-weight:700;margin-left:6px}}
 .tgt-stock{{margin-bottom:18px}} .tgt-stock:last-child{{margin-bottom:0}}
 .tgt-head{{display:flex;justify-content:space-between;font-weight:700;font-size:15px;padding:8px 0;border-bottom:1px solid #e8e8ee}}
 .tgt-row{{display:flex;justify-content:space-between;font-size:14px;padding:7px 0 7px 12px;border-bottom:1px solid #f5f5f8;color:#444}}
 .tgt-row.hit{{background:#fff8e1;font-weight:700;color:#8a6100;border-radius:6px}}
 .tgt-row.buy{{color:#d23f3f}} .tgt-row.buy .up,.tgt-row.buy .down{{color:inherit}}
 .grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:840px}}
 .card{{background:#fff;border-radius:16px;padding:24px;box-shadow:0 2px 10px rgba(0,0,0,.05)}}
 .card h2{{font-size:17px;margin:0 0 16px;padding-bottom:10px;border-bottom:2px solid #2c5fd0}}
 ul{{list-style:none;margin:0;padding:0}} li{{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid #f0f0f3;font-size:14px}}
 .lbl{{color:#555;white-space:nowrap}} .val{{font-weight:600;text-align:right}}
 .up{{color:#d23f3f}} .down{{color:#2c5fd0}} .warn{{cursor:help}}
 .noteline{{padding:4px 0 9px}} .note{{color:#999;font-size:12px;font-weight:400;line-height:1.5;text-align:left}}
 .stk{{display:flex;justify-content:space-between;gap:14px}} .stk span:first-child{{font-weight:500;color:#333}}
 .chart-card{{grid-column:1/-1}} .chart-card canvas{{max-height:340px}}
 @media(max-width:680px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<h1>📊 일일 시장 리포트</h1>
<div class="stamp">생성 {stamp} · {kr_date} 영업일 기준 · 등락률은 전일 대비</div>
{banner}{tgt_banner}
<div class="grid">
  <div class="card"><h2>🇰🇷 국내 코스피시장</h2><ul>{''.join(parts_kr)}</ul></div>
  <div class="card"><h2>🇺🇸 미국 나스닥시장</h2><ul>{''.join(parts_us)}</ul></div>
  <div class="card chart-card"><h2>📈 최근 1개월 추이 (시작일=100)</h2><canvas id="trend"></canvas></div>
  {tgt_card}
</div>
<script>
const CHART_DATA = {chart_json};
new Chart(document.getElementById('trend'), {{
  type: 'line',
  data: CHART_DATA,
  options: {{
    responsive: true,
    interaction: {{mode: 'index', intersect: false}},
    scales: {{
      x: {{type: 'category', ticks: {{maxTicksLimit: 10}}}},
      y: {{title: {{display: true, text: '상대지수 (시작일=100)'}}}}
    }},
    plugins: {{legend: {{position: 'top'}}}}
  }}
}});
</script>
</body></html>"""


if __name__ == "__main__":
    html = build_html()
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html 생성 완료")
