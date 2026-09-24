# 일일 시장 리포트 (미국 시장 하락 위험 신호등)

GitHub Actions가 평일 매일 아침 데이터를 수집 → `index.html` 생성 → GitHub Pages가 게시.

## 리포트 구성
- **🚨 미국 시장 하락 위험 신호등** (핵심): 시장에서 가장 많이 보는 하락 경고 지표 11개를
  4개 묶음으로 보여주고, 지표마다 **안정 / 주의 / 위험** 배지 + 쉬운 설명 + 판정 기준을 붙임.
  상단에 위험·주의·안정 개수 막대와 종합 판정(양호/주의/경계) 표시.
  | 묶음 | 지표 | 위험 기준 |
  |---|---|---|
  | 심리 | 공포지수 VIX | 30 이상 |
  | 심리 | VIX 기간구조 (VIX ÷ VIX3M) | 1.0 이상 (단기 공포 > 장기) |
  | 심리 | CNN 공포·탐욕 지수 | 25 미만 (75 이상은 과열 주의) |
  | 추세 | S&P500 vs 200일 이동평균 | 200일선 아래 |
  | 추세 | S&P500 / 나스닥 52주 고점 대비 낙폭 | -10% 이상 |
  | 신용·금리 | 장단기 금리차 10년−2년, 10년−3개월 | 마이너스(역전) |
  | 신용·금리 | 하이일드 채권 스프레드 | 6%p 이상 |
  | 경기·밸류 | 삼의 법칙 (실업률) | 0.5%p 이상 |
  | 경기·밸류 | 실러 CAPE | 35배 이상 |
- 보조 지표: 국고채 3년·CD 91일, 원/달러 환율 / 미 국채 3개월·2년·10년·30년(장기채, 전일 대비 bp 변화), 달러인덱스
- **모든 지표를 누르면 정의·의미(·판정 기준)가 토글로 펼쳐짐**. '설명 모두 펼치기' 버튼으로 한 번에 열고 닫기 가능
  - 설명 문구는 `market_report.py`의 `INFO` 사전에서 수정
- 등락률은 **전일(직전 영업일) 대비**
- **교차 검증**: 원/달러 환율을 yfinance ↔ 네이버 두 소스에서 대조
- 개별 종목 정보(보유종목 목표가, ETF 시세 등)는 표시하지 않음
- 지표 하나가 조회 실패해도 '조회 실패'로 표시하고 나머지는 정상 생성

## 데이터 소스
- yfinance: `^VIX`, `^VIX3M`, `^GSPC`, `^IXIC`, `KRW=X`, `DX-Y.NYB`, `^KS11` (미 국채는 재무부 실패 시 `^IRX`·`^TNX`·`^TYX`로 대체)
- 미 재무부 일일 국채 수익률 CSV: 미 국채 3개월·2년·10년·30년, 장단기 금리차
- 미 노동통계국(BLS) 실업률 → 삼의 법칙 직접 계산
- FRED `BAMLH0A0HYM2`(하이일드 스프레드): GitHub Actions에서는 키 없는 CSV가 자주 막힘.
  무료 API 키(fred.stlouisfed.org → My Account → API Keys)를 레포
  **Settings → Secrets and variables → Actions**에 `FRED_API_KEY`로 등록하면 안정적으로 조회됨
- CNN 공포·탐욕 지수 공개 JSON, multpl.com(실러 CAPE)
- 한국은행 ECOS(공개 sample 키, `ECOS_API_KEY` 시크릿 등록 시 개인 키 사용): 국고채 3년·CD 91일
  → 실패 시 네이버 모바일·네이버 금융 순으로 대체
- 네이버 금융: 환율 교차검증
- 신호 기준값은 `market_report.py`의 `us_risk_signals()`에서 바로 수정 가능

## 설치 (한 번만)
1. 새 GitHub 레포 생성 후 이 폴더 내용 전체 업로드 (또는 `git push`)
   - `report.yml`은 레포의 `.github/workflows/report.yml` 위치에 두어야 합니다.
2. 레포 **Settings → Pages → Source: Deploy from a branch → main / (root)** 선택
3. **Settings → Actions → General → Workflow permissions → Read and write** 체크
4. **Actions 탭 → market-report → Run workflow** 로 첫 실행
5. 잠시 뒤 `https://<아이디>.github.io/<레포명>/` 에서 리포트 확인

이후 **평일 매일 아침 7시(KST)** 자동 갱신됩니다. (한국장·미국장 마감이 모두 반영된 시점)

## 발송 주기 바꾸기
`.github/workflows/report.yml` 의 cron 수정 (UTC 기준)
- 매일: `0 22 * * *`
- 주 1회(월요일 아침): `0 22 * * 0`

## 로컬 테스트
```
pip install -r requirements.txt
python market_report.py
```
생성된 `index.html`을 브라우저로 열면 됩니다. (차트는 Chart.js CDN을 사용하므로 인터넷 연결 필요)
