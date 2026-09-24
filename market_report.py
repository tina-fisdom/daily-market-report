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
import os
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
# 한국은행 ECOS 817Y002(시장금리·일별) 항목코드 / 네이버 코드
KR_RATES = {
    "국고채 3년": {"ecos": "010200000", "naver_bond": "KR3YT=RR", "naver_old": "IRR_GOVT03Y"},
    "CD 91일": {"ecos": "010502000", "naver_bond": None, "naver_old": "IRR_CD91"},
}


def _ecos_rate(item):
    """한국은행 ECOS 일별 시장금리 최신값. 환경변수 ECOS_API_KEY가 없으면 공개 'sample' 키
    (최대 10행)를 쓰므로 최근 14일(영업일 10일 이내)만 조회."""
    key = os.environ.get("ECOS_API_KEY", "").strip() or "sample"
    end = datetime.date.today()
    start = end - datetime.timedelta(days=14)
    j = http_json(f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10/"
                  f"817Y002/D/{start:%Y%m%d}/{end:%Y%m%d}/{item}")
    rows = j["StatisticSearch"]["row"]
    last = rows[-1]
    t = last["TIME"]
    return float(last["DATA_VALUE"]), f"{t[:4]}-{t[4:6]}-{t[6:]}"


def _naver_bond_rate(code):
    j = http_json("https://m.stock.naver.com/front-api/marketIndex/prices"
                  f"?category=bond&reutersCode={code}&page=1&pageSize=10")
    first = j["result"][0]
    return num(first["closePrice"]), str(first["localTradedAt"])[:10]


def _naver_old_rate(code):
    r = HTTP.get("https://finance.naver.com/marketindex/interestDailyQuote.naver"
                 f"?marketindexCd={code}&page=1", timeout=15)
    r.raise_for_status()
    r.encoding = "euc-kr"
    nums = re.findall(r'<td class="num">([\d.]+)</td>', r.text)
    if not nums:
        raise ValueError("값 없음")
    return float(nums[0]), ""


def kr_rate(name):
    """국내 금리(%) 최신값: (값, 날짜). 소스를 차례로 시도하고 실패 사유는 로그로 남긴다."""
    src = KR_RATES[name]
    tries = [("ECOS", _ecos_rate, src["ecos"]),
             ("네이버 모바일", _naver_bond_rate, src["naver_bond"]),
             ("네이버", _naver_old_rate, src["naver_old"])]
    for label, fn, code in tries:
        if not code:
            continue
        try:
            return fn(code)
        except Exception as e:
            print(f"{name} {label} 실패:", e)
    raise ValueError(f"{name} 모든 소스 실패")


# ──────────────────────────────────────────────
# 미국 하락 위험 지표 — 외부 소스
# ──────────────────────────────────────────────
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def fred_last(series_id):
    """FRED 최신 관측값: (값, 'YYYY-MM-DD'). 결측치('.')는 건너뛴다.
    환경변수 FRED_API_KEY가 있으면 공식 API(api.stlouisfed.org)를,
    없으면 키 불필요 CSV(fred.stlouisfed.org — GitHub Actions에서 막히는 경우가 많음)를 쓴다."""
    start = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        j = http_json("https://api.stlouisfed.org/fred/series/observations"
                      f"?series_id={series_id}&api_key={key}&file_type=json"
                      f"&observation_start={start}&sort_order=desc&limit=10")
        for o in j["observations"]:
            if o["value"] not in ("", "."):
                return float(o["value"]), o["date"]
        raise ValueError(f"FRED {series_id} 값 없음")
    r = HTTP.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}",
                 timeout=25, headers={"User-Agent": BROWSER_UA, "Accept": "text/csv,*/*"})
    r.raise_for_status()
    for line in reversed(r.text.strip().splitlines()[1:]):
        d, _, v = line.partition(",")
        v = v.strip()
        if v and v != ".":
            return float(v), d.strip()
    raise ValueError(f"FRED {series_id} 값 없음")


def sahm_rule():
    """삼의 법칙 값을 미 노동통계국(BLS) 실업률(LNS14000000, 계절조정)로 직접 계산:
    최근 3개월 평균 실업률 − 직전 12개월 동안의 3개월 평균 중 최저치. 반환 (값, 'YYYY-MM')."""
    y = datetime.date.today().year
    r = HTTP.post("https://api.bls.gov/publicAPI/v2/timeseries/data/",
                  json={"seriesid": ["LNS14000000"], "startyear": str(y - 2), "endyear": str(y)},
                  timeout=20)
    r.raise_for_status()
    data = r.json()["Results"]["series"][0]["data"]
    pts = sorted((int(d["year"]), int(d["period"][1:]), float(d["value"]))
                 for d in data if d["period"].startswith("M") and d["period"] != "M13"
                 and d["value"] not in ("-", ""))
    rates = [v for _, _, v in pts]
    ma3 = [sum(rates[i - 2:i + 1]) / 3 for i in range(2, len(rates))]
    if len(ma3) < 13:
        raise ValueError("BLS 실업률 데이터 부족")
    yy, mm, _ = pts[-1]
    return ma3[-1] - min(ma3[-13:-1]), f"{yy}-{mm:02d}"


_CURVE = {}


def treasury_curve():
    """미 재무부 공식 일일 국채 수익률 곡선: (최신 {'3 Mo': 4.03, '2 Yr': ..}, 날짜, 전일 {..}).
    CSV는 최신 날짜가 맨 위. 연초엔 올해 데이터가 없을 수 있어 전년도까지 확인. 한 번만 받아 재사용."""
    if _CURVE:
        return _CURVE["v"], _CURVE["d"], _CURVE["prev"]

    def parse(head, row):
        vals = {}
        for k, v in zip(head, row):
            try:
                vals[k.strip()] = float(v)
            except ValueError:
                pass
        return vals

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
        head = rows[0]
        m, d, yy = rows[1][0].split("/")
        _CURVE.update(v=parse(head, rows[1]), d=f"{yy}-{m}-{d}",
                      prev=parse(head, rows[2]) if len(rows) > 2 else {})
        return _CURVE["v"], _CURVE["d"], _CURVE["prev"]
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

# 지표 이름 → (정의, 의미). 화면에서 지표를 누르면 펼쳐지는 설명.
INFO = {
    # ── 하락 위험 신호등 ──
    "공포지수 VIX": (
        "S&P500 지수옵션 가격을 바탕으로 시카고옵션거래소(CBOE)가 계산하는 "
        "'앞으로 30일 동안 예상되는 주가 변동폭'(연율, %)입니다.",
        "하락에 대비한 보험(풋옵션)을 사려는 사람이 많아질수록 보험료가 비싸지고 VIX가 오릅니다. "
        "보험료가 오른다는 건 그만큼 불안하다는 뜻이죠. 평소엔 12~20 사이, 2020년 코로나 폭락 때는 80을 넘었습니다. "
        "VIX가 급등하면 '지금 시장이 흔들리고 있다'는 신호입니다."),
    "VIX 기간구조 (VIX ÷ VIX3M)": (
        "30일 예상 변동성(VIX)을 3개월 예상 변동성(VIX3M)으로 나눈 값입니다.",
        "보통은 먼 미래가 더 불확실하니 3개월 쪽이 커서 1보다 작습니다(콘탱고). "
        "이게 1을 넘어 뒤집히면(백워데이션) '석 달 뒤보다 당장 다음 달이 더 무섭다'는 패닉 상태로, "
        "큰 하락장 한가운데서 주로 나타납니다. 다시 1 아래로 내려오면 공포가 가라앉는 신호로도 봅니다."),
    "CNN 공포·탐욕 지수": (
        "CNN이 주가 모멘텀, 52주 신고가·신저가 비율, 상승·하락 종목 거래량, 풋/콜 옵션 비율, "
        "정크본드 수요, VIX, 안전자산(채권) 선호 등 7개 지표를 합쳐 0~100으로 나타낸 투자심리 지수입니다.",
        "0에 가까울수록 공포, 100에 가까울수록 탐욕입니다. "
        "'남들이 탐욕스러울 때 두려워하라'는 버핏의 말처럼 극단적 탐욕(75 이상)은 과열과 되돌림을 경고하고, "
        "극단적 공포(25 미만)는 이미 투매가 진행 중이라는 뜻입니다. 역발상 투자자는 이 구간을 매수 기회로 살피기도 합니다."),
    "S&P500 vs 200일 이동평균": (
        "최근 200거래일(약 10개월) S&P500 종가의 평균과 현재 지수가 몇 % 떨어져 있는지를 봅니다.",
        "기관투자자들이 가장 많이 보는 '장기 추세선'입니다. 지수가 선 위에 있으면 상승 추세, "
        "선 아래로 내려가면 추세가 꺾였다고 보고 매도 물량이 늘기도 합니다. "
        "반대로 선보다 15% 이상 멀리 올라가 있으면 너무 빨리 달린 것이라 되돌림 가능성이 커집니다."),
    "S&P500 52주 고점 대비 낙폭": (
        "최근 52주(1년) 동안의 최고 종가에서 현재 지수가 몇 % 내려왔는지입니다.",
        "월가에서는 관례적으로 -10%를 '조정(correction)', -20%를 '약세장(bear market)'이라 부릅니다. "
        "고점 근처라면 아직 하락 전이고, 이미 -10%를 넘었다면 하락이 '진행 중'이라는 뜻입니다."),
    "나스닥 52주 고점 대비 낙폭": (
        "나스닥 종합지수의 최근 52주 최고 종가 대비 하락률입니다.",
        "기술·성장주 비중이 높아 금리와 경기 기대에 더 민감합니다. "
        "나스닥이 S&P500보다 먼저, 더 크게 빠지기 시작하면 위험을 감수하려는 분위기가 식고 있다는 신호입니다."),
    "장단기 금리차 (10년 − 2년)": (
        "미국 10년 만기 국채 금리에서 2년 만기 국채 금리를 뺀 값입니다(미 재무부 공시 기준).",
        "2년물은 앞으로의 기준금리 전망을, 10년물은 장기 성장과 물가 전망을 반영합니다. "
        "정상이라면 오래 빌려줄수록 이자가 높아 플러스입니다. 마이너스(역전)는 '곧 경기가 나빠져 금리를 내리게 될 것'이라는 시장의 베팅이죠. "
        "1980년 이후 미국 경기침체 앞에는 대부분 역전이 있었고, 실제 침체와 주가 하락은 역전이 풀리며 다시 플러스로 가파르게 벌어질 때 온 경우가 많았습니다."),
    "장단기 금리차 (10년 − 3개월)": (
        "미국 10년 만기 국채 금리에서 3개월 만기 국채 금리를 뺀 값입니다.",
        "뉴욕 연방준비은행의 경기침체 확률 모델이 쓰는 금리차입니다. 3개월물은 현재 기준금리를 거의 그대로 반영하므로, "
        "'지금의 통화정책이 장기 경제 전망에 비해 얼마나 빡빡한가'를 보여줍니다. 10년−2년과 함께 보면 신호가 더 선명해집니다."),
    "하이일드 채권 스프레드": (
        "신용등급 BB 이하(투기등급) 미국 회사채 금리가 같은 만기 국채 금리보다 얼마나 높은지입니다(ICE BofA 지수, FRED 공시).",
        "돈을 빌려주는 사람들이 기업 부실을 얼마나 걱정하는지 보여주는 '신용시장의 공포지수'입니다. "
        "채권 투자자는 주식 투자자보다 먼저 위험을 감지하는 경향이 있어, 스프레드가 빠르게 벌어지면 주가 하락의 선행 신호가 됩니다. "
        "2008년 금융위기 때는 20%p, 2020년 코로나 때는 10%p를 넘었습니다."),
    "삼의 법칙 (실업률)": (
        "최근 3개월 평균 실업률에서 직전 12개월 중 가장 낮았던 3개월 평균 실업률을 뺀 값입니다. "
        "연준 이코노미스트 출신 클로디아 삼(Claudia Sahm)이 만들었습니다(미 노동통계국 실업률로 계산).",
        "0.5%p 이상이면 경기침체가 이미 시작됐다고 봅니다. 1970년 이후 미국 경기침체를 거의 모두 초기에 잡아냈습니다. "
        "다만 '예고'가 아니라 '확인'하는 지표라 한 달에 한 번(고용보고서 발표 후) 갱신됩니다."),
    "실러 CAPE (경기조정 PER)": (
        "S&P500 지수를 최근 10년간 물가를 반영한 평균 주당순이익으로 나눈 값입니다. "
        "노벨경제학상 수상자 로버트 실러가 고안해 '실러 PER'이라고도 부릅니다.",
        "한 해 이익은 경기에 따라 들쭉날쭉하니 10년 평균으로 '진짜 몸값'을 보자는 지표입니다. 높을수록 비싸다는 뜻이고, "
        "1929년(약 30배)과 2000년(약 44배)처럼 높았던 시기 뒤에는 이후 10년 수익률이 낮았습니다. "
        "다만 타이밍 지표는 아니라 비싼 상태가 몇 년씩 이어지기도 합니다. '떨어질 때 얼마나 아플 수 있나'를 재는 척도로 보세요."),
    # ── 보조 지표 ──
    "국고채 3년": (
        "한국 정부가 발행한 만기 3년 국채의 시장 유통 금리입니다(한국은행 ECOS 시장금리 기준).",
        "한국 채권시장의 대표 금리로, 한국은행 기준금리 전망을 가장 민감하게 반영합니다. "
        "기준금리보다 낮아지면 시장이 금리 인하를 예상한다는 뜻입니다. "
        "주택담보대출(고정형)과 회사채 금리의 기준점이 되어 가계와 기업의 이자 부담에 직결됩니다."),
    "CD 91일": (
        "은행이 발행하는 만기 91일 양도성예금증서(CD)의 유통 금리입니다.",
        "대표적인 단기 시장금리로, 한국은행 기준금리와 거의 붙어 움직입니다. "
        "변동금리 대출과 기업 단기자금 금리의 기준으로 쓰여 '지금 당장의 돈값'을 보여줍니다."),
    "원/달러 환율": (
        "1달러를 사는 데 필요한 원화 금액입니다.",
        "오르면(원화 약세) 수입물가와 해외여행 비용이 오르고, 외국인 투자자는 환손실이 우려돼 한국 주식을 팔 유인이 커집니다. "
        "전 세계가 위험을 피할 때 급등하는 경향이 있어 한국 투자자에게는 또 하나의 불안 온도계입니다. "
        "달러 자산을 가진 사람에게는 환차익이 생깁니다."),
    "미 국채 3개월": (
        "미 재무부가 발행하는 만기 3개월 단기 국채(T-bill) 금리입니다.",
        "연준 기준금리와 거의 같이 움직여 '현재 달러의 돈값'을 보여줍니다. 달러 예금과 MMF 수익률도 이 수준을 따라갑니다."),
    "미 국채 2년": (
        "만기 2년 미국 국채 금리입니다.",
        "앞으로 1~2년 동안 연준이 금리를 어떻게 움직일지에 대한 시장 예상을 가장 잘 반영합니다. "
        "빠르게 떨어지면 '곧 금리를 내릴 만큼 경기가 식는다'는 기대가 커졌다는 뜻입니다."),
    "미 국채 10년": (
        "만기 10년 미국 국채 금리로, 전 세계 금융시장의 기준이 되는 장기 금리입니다.",
        "미국 주택담보대출 금리, 기업의 자금조달 비용, 주식 가치를 계산할 때의 할인율이 모두 여기에 연동됩니다. "
        "급등하면 미래 이익의 현재가치가 줄어 성장주·기술주에 부담이 되고, 경기 걱정으로 급락하면 안전자산 쏠림 신호입니다."),
    "미 국채 30년 (장기채)": (
        "만기 30년 미국 국채 금리로, 가장 긴 만기의 대표 장기채 금리입니다.",
        "장기 물가 전망과 미국 재정적자(국채 발행 부담)에 대한 걱정을 반영합니다. 5%를 넘어 오를 때마다 "
        "'채권시장이 미국 재정을 경고한다'는 해석이 나옵니다. 만기가 길수록 금리 변화에 가격이 크게 움직여, "
        "장기채에 투자했다면 금리 1%p 변화에 원금이 15~20%가량 오르내릴 수 있다는 점을 기억하세요."),
    "달러인덱스(DXY)": (
        "유로·엔·파운드·캐나다달러·스웨덴크로나·스위스프랑 6개 통화 대비 미 달러 가치를 지수화한 것입니다(1973년 = 100).",
        "달러가 강해진다는 건 전 세계 돈이 미국과 안전자산으로 몰린다는 뜻으로, 신흥국 증시와 원화에는 대개 부담입니다. "
        "짧은 기간에 급등하면 글로벌 위험회피 신호로 봅니다."),
}


def signal(group, name, value, level, criteria):
    return {"group": group, "name": name, "value": value, "level": level,
            "criteria": criteria}


def us_risk_signals():
    """미국 시장 하락 위험 지표 목록. 지표별로 실패해도 '조회 실패'로 남긴다.
    설명(정의·의미)은 INFO[지표 이름]에 있다."""
    out = []

    def add(group, name, criteria, fn):
        try:
            value, level = fn()
            out.append(signal(group, name, value, level, criteria))
        except Exception as e:
            print(name, "실패:", e)
            out.append(signal(group, name, "—", "na", criteria))

    # ── 심리 ──
    def vix():
        v, pct = prev_change(yf_close("^VIX"))
        lv = "ok" if v < 20 else ("warn" if v < 30 else "danger")
        return f"{v:.2f} {sign(pct)}", lv
    add("심리", "공포지수 VIX", "20 미만 안정 · 20~30 주의 · 30 이상 위험", vix)

    def vix_term():
        ratio = yf_last("^VIX") / yf_last("^VIX3M")
        lv = "ok" if ratio < 0.9 else ("warn" if ratio < 1.0 else "danger")
        return f"{ratio:.2f}", lv
    add("심리", "VIX 기간구조 (VIX ÷ VIX3M)",
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
        "+3~15% 안정 · 0~3% 또는 15% 이상 주의 · 선 아래 위험", ma200)

    def drawdown(close):
        last, high = float(close.iloc[-1]), float(close.max())
        dd = (last / high - 1) * 100
        lv = "ok" if dd > -5 else ("warn" if dd > -10 else "danger")
        return f"{dd:+.1f}% (고점 {high:,.0f})", lv
    add("추세", "S&P500 52주 고점 대비 낙폭",
        "-5% 이내 안정 · -5~-10% 주의 · -10% 이상 위험", lambda: drawdown(spx))
    add("추세", "나스닥 52주 고점 대비 낙폭",
        "-5% 이내 안정 · -5~-10% 주의 · -10% 이상 위험", lambda: drawdown(ndx))

    # ── 신용·금리 ──
    def spread(long_k, short_k):
        cur, d, _ = treasury_curve()
        v = cur[long_k] - cur[short_k]
        lv = "danger" if v < 0 else ("warn" if v < 0.5 else "ok")
        return f"{v:+.2f}%p <small>({d})</small>", lv

    def t10y3m():
        try:
            return spread("10 Yr", "3 Mo")
        except Exception as e:                     # 재무부 실패 시 yfinance로 대체
            print("재무부 10년-3개월 실패, yfinance 사용:", e)
            v = yf_last("^TNX") - yf_last("^IRX")
            lv = "danger" if v < 0 else ("warn" if v < 0.5 else "ok")
            return f"{v:+.2f}%p", lv

    add("신용·금리", "장단기 금리차 (10년 − 2년)",
        "0.5%p 이상 안정 · 0~0.5%p 주의 · 마이너스(역전) 위험",
        lambda: spread("10 Yr", "2 Yr"))
    add("신용·금리", "장단기 금리차 (10년 − 3개월)",
        "0.5%p 이상 안정 · 0~0.5%p 주의 · 마이너스(역전) 위험", t10y3m)

    def hy_spread():
        v, d = fred_last("BAMLH0A0HYM2")
        lv = "ok" if v < 4 else ("warn" if v < 6 else "danger")
        return f"{v:.2f}%p <small>({d})</small>", lv
    add("신용·금리", "하이일드 채권 스프레드",
        "4%p 미만 안정 · 4~6%p 주의 · 6%p 이상 위험", hy_spread)

    # ── 경기·밸류에이션 ──
    def sahm():
        v, d = sahm_rule()
        lv = "ok" if v < 0.3 else ("warn" if v < 0.5 else "danger")
        return f"{v:.2f}%p <small>({d})</small>", lv
    add("경기·밸류에이션", "삼의 법칙 (실업률)",
        "0.3%p 미만 안정 · 0.3~0.5%p 주의 · 0.5%p 이상 위험", sahm)

    def cape():
        v = shiller_cape()
        lv = "ok" if v < 25 else ("warn" if v < 35 else "danger")
        return f"{v:.1f}배", lv
    add("경기·밸류에이션", "실러 CAPE (경기조정 PER)",
        "25배 미만 안정 · 25~35배 주의 · 35배 이상 위험(고평가) · 역사적 평균 약 17배", cape)

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
def info_body(name, criteria=""):
    """토글을 열면 보이는 정의·의미·기준 블록."""
    d, m = INFO.get(name, ("", ""))
    crit = f'<p class="crit"><b>판정 기준</b>{criteria}</p>' if criteria else ""
    return (f'<div class="sig-body"><p><b>정의</b>{d}</p>'
            f'<p><b>의미</b>{m}</p>{crit}</div>')


def row(name, value, warn=False, tooltip="", badge=""):
    """보조 지표 한 줄 — 클릭하면 정의·의미가 펼쳐진다."""
    mark = f'<span class="warn" title="{tooltip}">⚠️</span> ' if warn else ""
    return (f'<details class="sig"><summary><span class="sig-name">{name}</span>'
            f'<span class="sig-val">{mark}{value}{badge}</span></summary>'
            f'{info_body(name)}</details>')


def bp(diff):
    """금리 변화(%p) → '▲ 3bp' 형식 (1bp = 0.01%p)."""
    b = round(diff * 100)
    if b == 0:
        return '<span class="flat">(0bp)</span>'
    cls, arrow = ("up", "▲") if b > 0 else ("down", "▼")
    return f'<span class="{cls}">({arrow}{abs(b)}bp)</span>'


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
            f'<details class="sig"><summary><span class="sig-name">{s["name"]}</span>'
            f'<span class="sig-val">{s["value"]}'
            f'<span class="badge {s["level"]}">{LEVEL_TEXT[s["level"]]}</span></span></summary>'
            f'{info_body(s["name"], s["criteria"])}</details>'
            for s in rows)
        groups_html.append(f'<div class="sig-group"><h3>{g} <span>· {desc}</span></h3>{items}</div>')

    card = (f'<div class="card full"><h2>🚨 미국 시장 하락 위험 신호등</h2>'
            f'{summary}<p class="hint"><span>지표를 누르면 정의와 의미가 펼쳐집니다.</span>'
            f'<button type="button" class="toggle-all">설명 모두 펼치기</button></p>{"".join(groups_html)}'
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
    for name in ("국고채 3년", "CD 91일"):
        try:
            v, d = kr_rate(name)
            date_txt = f" <small>({d})</small>" if d else ""
            parts_kr.append(row(name, f"{v:.2f}%{date_txt}"))
        except Exception as e:
            print(name, "실패:", e)
            parts_kr.append(row(name, "조회 실패"))
    try:
        last_fx, fx_pct = prev_change(series_map["KRW=X"])
        w, tip = warn_args(checks, "원/달러")
        parts_kr.append(row("원/달러 환율", f"{last_fx:,.1f} {sign(fx_pct)}", w, tip))
    except Exception as e:
        print("환율 실패:", e)

    # ── 미국 금리 (재무부 공식 수익률, 실패 시 yfinance) ──
    US_RATES = [("미 국채 3개월", "3 Mo", "^IRX"), ("미 국채 2년", "2 Yr", None),
                ("미 국채 10년", "10 Yr", "^TNX"), ("미 국채 30년 (장기채)", "30 Yr", "^TYX")]
    try:
        cur, cur_d, prev = treasury_curve()
    except Exception as e:
        print("재무부 수익률 실패, yfinance 사용:", e)
        cur, cur_d, prev = {}, "", {}
    for name, key, tk in US_RATES:
        try:
            if key in cur:
                v = cur[key]
                chg = f" {bp(v - prev[key])}" if key in prev else ""
                parts_us.append(row(name, f"{v:.2f}%{chg}"))
            elif tk:
                close = yf_close(tk)
                v, pv = float(close.iloc[-1]), float(close.iloc[-2])
                parts_us.append(row(name, f"{v:.2f}% {bp(v - pv)}"))
            else:
                raise ValueError("데이터 없음")
        except Exception as e:
            print(name, "실패:", e)
            parts_us.append(row(name, "조회 실패"))
    try:
        parts_us.append(row("달러인덱스(DXY)", f"{yf_last('DX-Y.NYB'):.2f}"))
    except Exception as e:
        print("달러인덱스 실패:", e)
    us_rate_note = f'<p class="src">미 국채: 미 재무부 공시 {cur_d} · 괄호는 전일 대비 변화(bp=0.01%p)</p>' if cur_d else ""

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
 .sig{{border-bottom:1px solid #f0f0f3}}
 .sig summary{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px 12px;
   padding:10px 0;font-size:14px;cursor:pointer;list-style:none}}
 .sig summary::-webkit-details-marker{{display:none}}
 .sig summary:hover .sig-name{{color:#2c5fd0}}
 .sig-name{{font-weight:600}}
 .sig-name::before{{content:"▸";display:inline-block;width:14px;color:#aab;transition:transform .15s}}
 .sig[open] .sig-name::before{{transform:rotate(90deg)}}
 .sig-val{{font-weight:600;display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}}
 .sig-val small,.flat{{color:#999;font-weight:400}}
 .sig-body{{background:#f7f8fb;border-radius:10px;padding:12px 14px;margin:0 0 12px 14px;font-size:13px;line-height:1.65;color:#444}}
 .sig-body p{{margin:0 0 8px}} .sig-body p:last-child{{margin:0}}
 .sig-body b{{display:inline-block;min-width:62px;color:#2c5fd0;font-size:12px}}
 .sig-body .crit{{color:#777}}
 .hint{{font-size:12px;color:#999;margin:-6px 0 12px;display:flex;justify-content:space-between;align-items:center;gap:8px}}
 .toggle-all{{font:inherit;font-size:12px;border:1px solid #d6dbe6;background:#fff;color:#2c5fd0;border-radius:999px;padding:3px 10px;cursor:pointer}}
 .src{{font-size:11.5px;color:#aaa;margin:8px 0 0}}
 .badge{{font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;white-space:nowrap}}
 .badge.ok{{background:#e3f4e9;color:#1d7a3d}} .badge.warn{{background:#fff0d1;color:#a05c00}}
 .badge.danger{{background:#fde1df;color:#b42318}} .badge.na{{background:#eee;color:#888}}
 .disclaimer{{color:#999;font-size:12px;line-height:1.5;margin-top:6px}}
 @media(max-width:680px){{
   body{{padding:14px}}
   .grid{{grid-template-columns:1fr;gap:14px}}
   .card{{padding:16px;border-radius:14px}}
   h1{{font-size:20px}}
   .sig summary{{font-size:13px}}
   .sig-body{{margin-left:0}}
 }}
</style></head><body>
<h1>📊 일일 시장 리포트</h1>
<div class="stamp">생성 {stamp} · {basis} 기준 · 등락률은 전일 대비</div>
{banner}{r_banner}
<div class="grid">
  {r_card}
  <div class="card"><h2>🇰🇷 국내 지표</h2>{''.join(parts_kr)}</div>
  <div class="card"><h2>🇺🇸 미국 금리·달러</h2>{''.join(parts_us)}{us_rate_note}</div>
</div>
<script>
 // '모두 펼치기' 버튼: 페이지의 모든 지표 설명을 한 번에 열고 닫는다
 document.querySelectorAll('.toggle-all').forEach(function (btn) {{
   btn.addEventListener('click', function () {{
     var all = document.querySelectorAll('details.sig');
     var open = btn.dataset.open !== '1';
     all.forEach(function (d) {{ d.open = open; }});
     document.querySelectorAll('.toggle-all').forEach(function (b) {{
       b.dataset.open = open ? '1' : '0';
       b.textContent = open ? '설명 모두 접기' : '설명 모두 펼치기';
     }});
   }});
 }});
</script>
</body></html>"""


if __name__ == "__main__":
    html = build_html()
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html 생성 완료")
