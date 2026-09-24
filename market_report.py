# -*- coding: utf-8 -*-
"""
일일 시장 리포트 생성기
- 핵심: 🚨 미국 시장 하락 위험 신호등 — 시장 참여자들이 가장 많이 보는 지표를
        심리 / 추세 / 신용·금리 / 경기·밸류에이션 4개 묶음으로 보여주고
        지표마다 안정·주의·위험 신호를 붙인다.
- 보조 지표: 국고채 3년·CD 91일, 원/달러 환율 / 미 국채 3개월·10년, 달러인덱스
- 등락률: 전일(직전 영업일) 대비
- 교차 검증: 원/달러 환율을 yfinance ↔ 네이버 두 소스에서 대조
  → 오차 초과 시 ⚠️ 표시 후 그대로 게시 (게시 중단 없음)
- 결과물: index.html  (GitHub Pages가 그대로 게시)

실행: python market_report.py
※ 데이터 소스는 외부 서버라 항목별로 try/except로 감쌌습니다.
   한 곳이 실패해도 나머지 리포트는 정상 생성됩니다.
"""

import csv
import datetime
import io
import re

import requests
import yfinance as yf

HTTP = requests.Session()
HTTP.trust_env = False          # 로컬 .netrc 간섭 회피
HTTP.headers.update({"User-Agent": "Mozilla/5.0"})


def http_json(url, **kw):
    r = HTTP.get(url, timeout=15, **kw)
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


# ──────────────────────────────────────────────
# 국내 보조지표 — 네이버 금융 공개 API
# ──────────────────────────────────────────────
def kr_rate(marketindex_cd):
    """네이버 시장지표 금리(%) 최신값. 예: IRR_GOVT03Y(국고채 3년), IRR_CD91(CD 91일).
    ※ 국고채 1년/10년물은 네이버 미제공 — 단기금리는 CD 91일물로 대체."""
    r = HTTP.get("https://finance.naver.com/marketindex/interestDailyQuote.naver"
                 f"?marketindexCd={marketindex_cd}&page=1", timeout=15)
    r.raise_for_status()
    r.encoding = "euc-kr"
    nums = re.findall(r'<td class="num">([\d.]+)</td>', r.text)
    return float(nums[0]) if nums else None


# ──────────────────────────────────────────────
# 미국 하락 위험 지표 — 외부 소스
# ──────────────────────────────────────────────
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def fred_last(series_id):
    """FRED 공개 CSV(키 불필요)의 최신 관측값: (값, 'YYYY-MM-DD').
    결측치('.')는 건너뛴다. 클라우드 IP에서 응답이 느릴 때가 있어 2회까지 재시도."""
    start = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    err = None
    for _ in range(2):
        try:
            r = HTTP.get(url, timeout=40, headers={"User-Agent": BROWSER_UA,
                                                   "Accept": "text/csv,*/*"})
            r.raise_for_status()
            for line in reversed(r.text.strip().splitlines()[1:]):
                d, _, v = line.partition(",")
                v = v.strip()
                if v and v != ".":
                    return float(v), d.strip()
            raise ValueError(f"FRED {series_id} 값 없음")
        except Exception as e:
            err = e
    raise err


def treasury_curve():
    """미 재무부 공식 일일 국채 수익률 곡선 최신 행: ({'3 Mo': 4.03, '2 Yr': ..., '10 Yr': ...}, 날짜).
    CSV는 최신 날짜가 맨 위. 연초엔 올해 데이터가 없을 수 있어 전년도까지 확인."""
    year = datetime.date.today().year
    for y in (year, year - 1):
        r = HTTP.get("https://home.treasury.gov/resource-center/data-chart-center/"
                     f"interest-rates/daily-treasury-rates.csv/{y}/all"
                     f"?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv",
                     timeout=20, headers={"User-Agent": BROWSER_UA})
        r.raise_for_status()
        rows = list(csv.reader(io.StringIO(r.text.lstrip("\ufeff"))))
        if len(rows) < 2:
            continue
        head, first = rows[0], rows[1]
        vals = {}
        for k, v in zip(head, first):
            try:
                vals[k.strip()] = float(v)
            except ValueError:
                pass
        m, d, yy = first[0].split("/")
        return vals, f"{yy}-{m}-{d}"
    raise ValueError("재무부 수익률 데이터 없음")


def cnn_fear_greed():
    """CNN 공포·탐욕 지수 (0~100): (점수, 등급 문자열)."""
    j = http_json("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                           "Referer": "https://edition.cnn.com/",
                           "Origin": "https://edition.cnn.com"})
    fg = j["fear_and_greed"]
    return float(fg["score"]), str(fg.get("rating", ""))


def shiller_cape():
    """실러 CAPE (multpl.com 현재값)."""
    r = HTTP.get("https://www.multpl.com/shiller-pe", timeout=15,
                 headers={"User-Agent": BROWSER_UA})
    r.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", r.text)
    m = re.search(r"Current\s+Shiller\s+PE\s+Ratio\s*:\s*([\d.]+)", text)
    if not m:
        raise ValueError("CAPE 값 파싱 실패")
    return float(m.group(1))


# ──────────────────────────────────────────────
# 신호 판정 — level: ok(안정) / warn(주의) / danger(위험) / na(조회 실패)
# ──────────────────────────────────────────────
LEVEL_TEXT = {"ok": "안정", "warn": "주의", "danger": "위험", "na": "조회 실패"}

# 묶음 순서 = 화면 순서
GROUPS = [
    ("심리", "시장 참여자들이 얼마나 겁먹었나"),
    ("추세", "주가가 이미 꺾이고 있나"),
    ("신용·금리", "돈줄이 조이고 있나"),
    ("경기·밸류에이션", "경기와 가격 수준은 버틸 만한가"),
]


def signal(group, name, value, level, note, criteria):
    return {"group": group, "name": name, "value": value, "level": level,
            "note": note, "criteria": criteria}


def us_risk_signals():
    """미국 시장 하락 위험 지표 목록. 지표별로 실패해도 '조회 실패'로 남긴다."""
    out = []

    def add(group, name, note, criteria, fn):
        try:
            value, level = fn()
            out.append(signal(group, name, value, level, note, criteria))
        except Exception as e:
            print(name, "실패:", e)
            out.append(signal(group, name, "—", "na", note, criteria))

    # ── 심리 ──
    def vix():
        v, pct = prev_change(yf_close("^VIX"))
        lv = "ok" if v < 20 else ("warn" if v < 30 else "danger")
        return f"{v:.2f} {sign(pct)}", lv
    add("심리", "공포지수 VIX",
        "S&P500 옵션 가격에서 뽑아낸 '향후 30일 예상 흔들림'. 시장의 체온계라고 보면 됩니다.",
        "20 미만 안정 · 20~30 주의 · 30 이상 위험", vix)

    def vix_term():
        ratio = yf_last("^VIX") / yf_last("^VIX3M")
        lv = "ok" if ratio < 0.9 else ("warn" if ratio < 1.0 else "danger")
        return f"{ratio:.2f}", lv
    add("심리", "VIX 기간구조 (VIX ÷ VIX3M)",
        "평소엔 '먼 미래'가 더 불안해 1보다 작습니다. 1을 넘으면 '지금 당장'이 더 무섭다는 뜻 — 급락장의 전형적 신호.",
        "0.9 미만 안정 · 0.9~1.0 주의 · 1.0 이상 위험", vix_term)

    def fear_greed():
        score, rating = cnn_fear_greed()
        if score < 25:
            lv = "danger"
        elif score < 45 or score >= 75:
            lv = "warn"
        else:
            lv = "ok"
        rating_ko = {"extreme fear": "극단적 공포", "fear": "공포", "neutral": "중립",
                     "greed": "탐욕", "extreme greed": "극단적 탐욕"}.get(rating.lower(), rating)
        return f"{score:.0f} ({rating_ko})", lv
    add("심리", "CNN 공포·탐욕 지수",
        "7개 시장 지표를 0~100으로 합친 투자 심리 온도계. 너무 낮으면 투매, 너무 높으면 과열(되돌림 위험)입니다.",
        "25 미만 위험 · 25~45 주의 · 45~75 안정 · 75 이상 과열 주의", fear_greed)

    # ── 추세 (1년치 종가 한 번씩만 받아 재사용) ──
    spx = ndx = None
    try:
        spx = yf_close("^GSPC", "1y")
    except Exception as e:
        print("S&P500 1년 조회 실패:", e)
    try:
        ndx = yf_close("^IXIC", "1y")
    except Exception as e:
        print("나스닥 1년 조회 실패:", e)

    def ma200():
        last, ma = float(spx.iloc[-1]), float(spx.iloc[-200:].mean())
        gap = (last / ma - 1) * 100
        lv = "danger" if gap < 0 else ("warn" if gap < 3 or gap >= 15 else "ok")
        return f"{last:,.0f} (200일선 대비 {gap:+.1f}%)", lv
    add("추세", "S&P500 vs 200일 이동평균",
        "200일선은 '1년 가까운 평균 가격'. 주가가 이 선 아래로 내려가면 장기 추세가 꺾였다고 봅니다. 너무 멀리 위에 있어도 과열.",
        "+3~15% 안정 · 0~3% 또는 15% 이상 주의 · 선 아래 위험", ma200)

    def drawdown(close):
        last, high = float(close.iloc[-1]), float(close.max())
        dd = (last / high - 1) * 100
        lv = "ok" if dd > -5 else ("warn" if dd > -10 else "danger")
        return f"{dd:+.1f}% (고점 {high:,.0f})", lv
    add("추세", "S&P500 52주 고점 대비 낙폭",
        "최근 1년 최고점에서 얼마나 내려왔나. -10%는 '조정', -20%는 '약세장'이라고 부릅니다.",
        "-5% 이내 안정 · -5~-10% 주의 · -10% 이상 위험", lambda: drawdown(spx))
    add("추세", "나스닥 52주 고점 대비 낙폭",
        "기술주 비중이 큰 나스닥은 S&P500보다 먼저, 더 크게 흔들리는 경향이 있습니다.",
        "-5% 이내 안정 · -5~-10% 주의 · -10% 이상 위험", lambda: drawdown(ndx))

    # ── 신용·금리 ──
    curve = {}

    def spread(long_k, short_k):
        if not curve:
            curve["v"], curve["d"] = treasury_curve()
        v = curve["v"][long_k] - curve["v"][short_k]
        lv = "danger" if v < 0 else ("warn" if v < 0.5 else "ok")
        return f"{v:+.2f}%p <small>({curve['d']})</small>", lv

    def t10y3m():
        try:
            return spread("10 Yr", "3 Mo")
        except Exception as e:                     # 재무부 실패 시 yfinance로 대체
            print("재무부 10년-3개월 실패, yfinance 사용:", e)
            v = yf_last("^TNX") - yf_last("^IRX")
            lv = "danger" if v < 0 else ("warn" if v < 0.5 else "ok")
            return f"{v:+.2f}%p", lv

    add("신용·금리", "장단기 금리차 (10년 − 2년)",
        "정상이라면 오래 빌려줄수록 이자가 높습니다. 이게 뒤집히면(역전) 시장이 경기 침체를 예상한다는 뜻 — 역전 해소 직후가 오히려 위험했던 적이 많습니다.",
        "0.5%p 이상 안정 · 0~0.5%p 주의 · 마이너스(역전) 위험",
        lambda: spread("10 Yr", "2 Yr"))
    add("신용·금리", "장단기 금리차 (10년 − 3개월)",
        "미 연준이 경기침체 예측에 가장 신뢰하는 금리차. 10년−2년과 함께 보면 신호가 더 선명해집니다.",
        "0.5%p 이상 안정 · 0~0.5%p 주의 · 마이너스(역전) 위험", t10y3m)

    def hy_spread():
        v, d = fred_last("BAMLH0A0HYM2")
        lv = "ok" if v < 4 else ("warn" if v < 6 else "danger")
        return f"{v:.2f}%p <small>({d})</small>", lv
    add("신용·금리", "하이일드 채권 스프레드",
        "신용등급 낮은 회사가 국채보다 이자를 얼마나 더 줘야 돈을 빌릴 수 있나. 벌어지면 '돈 빌려주기 무섭다'는 신호로, 주식보다 먼저 움직이곤 합니다.",
        "4%p 미만 안정 · 4~6%p 주의 · 6%p 이상 위험", hy_spread)

    # ── 경기·밸류에이션 ──
    def sahm():
        v, d = fred_last("SAHMREALTIME")
        lv = "ok" if v < 0.3 else ("warn" if v < 0.5 else "danger")
        return f"{v:.2f}%p <small>({d[:7]})</small>", lv
    add("경기·밸류에이션", "삼의 법칙 (실업률)",
        "최근 3개월 평균 실업률이 지난 1년 최저치보다 0.5%p 이상 오르면 경기침체가 시작됐다고 보는 규칙. 월 1회 갱신.",
        "0.3%p 미만 안정 · 0.3~0.5%p 주의 · 0.5%p 이상 위험", sahm)

    def cape():
        v = shiller_cape()
        lv = "ok" if v < 25 else ("warn" if v < 35 else "danger")
        return f"{v:.1f}배", lv
    add("경기·밸류에이션", "실러 CAPE (경기조정 PER)",
        "최근 10년 평균 이익(물가 반영) 대비 주가 수준. 당장의 하락 신호라기보다 '떨어질 때 얼마나 아플 수 있나'를 보여주는 장기 지표 (역사적 평균 약 17배).",
        "25배 미만 안정 · 25~35배 주의 · 35배 이상 위험(고평가)", cape)

    us_date = spx.index[-1].strftime("%Y-%m-%d") if spx is not None and len(spx) else ""
    return out, us_date


# ──────────────────────────────────────────────
# 교차 검증 (yfinance ↔ 네이버) — 원/달러 환율
# ──────────────────────────────────────────────
def naver_fx_last():
    try:
        j = http_json("https://m.stock.naver.com/front-api/marketIndex/prices"
                      "?category=exchange&reutersCode=FX_USDKRW&page=1&pageSize=10")
        first = j["result"][0]
        return num(first["closePrice"]), str(first["localTradedAt"])[:10]
    except Exception as e:
        print("네이버 환율 조회 실패:", e)
        return None


def cross_check(label, yf_series, secondary, tol_pct, match_date=True):
    """
    yf_series: yfinance 종가 시리즈(날짜 인덱스), secondary: (값, 날짜) 또는 None.
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
    checks.append(cross_check("원/달러", series_map.get("KRW=X"),
                              naver_fx_last(), 1.0, match_date=False))
    return checks


# ──────────────────────────────────────────────
# HTML 렌더링
# ──────────────────────────────────────────────
def li(label, value, warn=False, tooltip=""):
    mark = f'<span class="warn" title="{tooltip}">⚠️</span> ' if warn else ""
    return f'<li><span class="lbl">{label}</span><span class="val">{mark}{value}</span></li>'


def sign(pct):
    arrow = "▲" if pct >= 0 else "▼"
    cls = "up" if pct >= 0 else "down"
    return f'<span class="{cls}">{arrow} {abs(pct):.2f}%</span>'


def risk_card(signals):
    """하락 위험 신호등 카드 HTML과 상단 요약 배너 HTML 반환."""
    counts = {k: sum(1 for s in signals if s["level"] == k)
              for k in ("danger", "warn", "ok", "na")}
    valid = len(signals) - counts["na"]

    if counts["danger"] >= 3:
        verdict, vcls = "경계 — 위험 신호가 여러 곳에서 켜졌습니다", "danger"
    elif counts["danger"] >= 1 or counts["warn"] >= 4:
        verdict, vcls = "주의 — 일부 지표에서 경고등이 켜졌습니다", "warn"
    else:
        verdict, vcls = "양호 — 뚜렷한 하락 신호는 없습니다", "ok"

    bar = "".join(
        f'<span class="seg {k}" style="flex:{counts[k]}"></span>'
        for k in ("danger", "warn", "ok") if counts[k])
    summary = (f'<div class="risk-sum {vcls}"><div class="risk-verdict">{verdict}</div>'
               f'<div class="risk-bar">{bar}</div>'
               f'<div class="risk-counts"><b class="c-danger">위험 {counts["danger"]}</b> · '
               f'<b class="c-warn">주의 {counts["warn"]}</b> · '
               f'<b class="c-ok">안정 {counts["ok"]}</b> / 조회된 {valid}개 지표'
               + (f' (조회 실패 {counts["na"]})' if counts["na"] else "") +
               '</div></div>')

    groups_html = []
    for g, desc in GROUPS:
        rows = [s for s in signals if s["group"] == g]
        if not rows:
            continue
        items = "".join(
            f'<div class="sig"><div class="sig-top">'
            f'<span class="sig-name">{s["name"]}</span>'
            f'<span class="sig-val">{s["value"]}'
            f'<span class="badge {s["level"]}">{LEVEL_TEXT[s["level"]]}</span></span></div>'
            f'<div class="sig-note">{s["note"]}</div>'
            f'<div class="sig-crit">기준: {s["criteria"]}</div></div>'
            for s in rows)
        groups_html.append(f'<div class="sig-group"><h3>{g} <span>· {desc}</span></h3>{items}</div>')

    card = (f'<div class="card full"><h2>🚨 미국 시장 하락 위험 신호등</h2>'
            f'{summary}{"".join(groups_html)}'
            f'<div class="disclaimer">※ 각 지표는 하락을 \'예언\'하지 않습니다. '
            f'여러 신호가 동시에 켜질 때 대비 수준을 높이는 참고용 체크리스트로 활용하세요.</div></div>')
    banner = {"danger": '<div class="vbanner vdanger">🚨 하락 위험 신호 다수</div>',
              "warn": '<div class="vbanner vcaution">⚠️ 하락 주의 신호 있음</div>',
              "ok": ""}[vcls]
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

    # 핵심 시리즈는 한 번만 받아 등락률·교차검증·날짜표시에 재사용
    series_map = {}
    for tk in ["^KS11", "KRW=X"]:
        try:
            series_map[tk] = yf_close(tk)
        except Exception as e:
            print(tk, "조회 실패:", e)
            series_map[tk] = None

    checks = run_validations(series_map)

    kr_date = ""
    if series_map.get("^KS11") is not None and len(series_map["^KS11"]):
        kr_date = series_map["^KS11"].index[-1].strftime("%Y-%m-%d")

    # ── 미국 하락 위험 신호등 (핵심) ──
    signals, us_date = us_risk_signals()
    r_card, r_banner = risk_card(signals)

    # ── 국내 보조지표 ──
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

    # ── 미국 보조지표 ──
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

    banner = validation_banner(checks)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    basis = " · ".join(x for x in (f"국내 {kr_date}" if kr_date else "",
                                   f"미국 {us_date}" if us_date else "") if x)

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>일일 시장 리포트</title>
<style>
 *{{box-sizing:border-box}}
 body{{font-family:'Pretendard',system-ui,sans-serif;background:#f5f6f8;color:#1a1a2e;margin:0;padding:24px}}
 h1{{font-size:22px;margin:0 0 4px}} .stamp{{color:#888;font-size:13px;margin-bottom:10px}}
 .vbanner{{display:inline-block;font-size:13px;padding:6px 12px;border-radius:8px;margin:0 6px 16px 0}}
 .vok{{background:#e8f5ec;color:#1d7a3d}} .vwarn{{background:#fdf0e0;color:#a05c00}}
 .vcaution{{background:#fff3cd;color:#8a6100;font-weight:700}} .vdanger{{background:#fde8e8;color:#b42318;font-weight:700}}
 .grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;max-width:840px}}
 .card{{background:#fff;border-radius:16px;padding:22px;box-shadow:0 2px 10px rgba(0,0,0,.05)}}
 .card h2{{font-size:17px;margin:0 0 14px;padding-bottom:10px;border-bottom:2px solid #2c5fd0}}
 .full{{grid-column:1/-1}}
 ul{{list-style:none;margin:0;padding:0}} li{{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid #f0f0f3;font-size:14px}}
 .lbl{{color:#555}} .val{{font-weight:600;text-align:right}}
 .up{{color:#d23f3f}} .down{{color:#2c5fd0}} .warn{{cursor:help}}
 .risk-sum{{border-radius:12px;padding:14px 16px;margin-bottom:18px;background:#f7f8fa}}
 .risk-sum.ok{{background:#eef8f1}} .risk-sum.warn{{background:#fff8e6}} .risk-sum.danger{{background:#fdeeee}}
 .risk-verdict{{font-weight:700;font-size:15px;margin-bottom:10px}}
 .risk-bar{{display:flex;height:10px;border-radius:5px;overflow:hidden;gap:2px;margin-bottom:8px}}
 .seg.danger{{background:#d92d20}} .seg.warn{{background:#f5a524}} .seg.ok{{background:#2e9e5b}}
 .risk-counts{{font-size:13px;color:#555}}
 .c-danger{{color:#b42318}} .c-warn{{color:#a05c00}} .c-ok{{color:#1d7a3d}}
 .sig-group{{margin-bottom:18px}} .sig-group:last-of-type{{margin-bottom:8px}}
 .sig-group h3{{font-size:15px;margin:0 0 4px;color:#2c5fd0}} .sig-group h3 span{{font-size:12px;color:#999;font-weight:400}}
 .sig{{padding:10px 0;border-bottom:1px solid #f0f0f3}}
 .sig-top{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px 12px;font-size:14px}}
 .sig-name{{font-weight:600}} .sig-val{{font-weight:600;display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}}
 .sig-val small{{color:#999;font-weight:400}}
 .sig-note{{color:#777;font-size:12px;line-height:1.55;margin-top:5px}}
 .sig-crit{{color:#aaa;font-size:11.5px;margin-top:3px}}
 .badge{{font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;white-space:nowrap}}
 .badge.ok{{background:#e3f4e9;color:#1d7a3d}} .badge.warn{{background:#fff0d1;color:#a05c00}}
 .badge.danger{{background:#fde1df;color:#b42318}} .badge.na{{background:#eee;color:#888}}
 .disclaimer{{color:#999;font-size:12px;line-height:1.5;margin-top:6px}}
 @media(max-width:680px){{
   body{{padding:14px}}
   .grid{{grid-template-columns:1fr;gap:14px}}
   .card{{padding:16px;border-radius:14px}}
   h1{{font-size:20px}}
   li,.sig-top{{font-size:13px}}
 }}
</style></head><body>
<h1>📊 일일 시장 리포트</h1>
<div class="stamp">생성 {stamp} · {basis} 기준 · 등락률은 전일 대비</div>
{banner}{r_banner}
<div class="grid">
  {r_card}
  <div class="card"><h2>🇰🇷 국내 지표</h2><ul>{''.join(parts_kr)}</ul></div>
  <div class="card"><h2>🇺🇸 미국 금리·달러</h2><ul>{''.join(parts_us)}</ul></div>
</div>
</body></html>"""


if __name__ == "__main__":
    html = build_html()
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html 생성 완료")
