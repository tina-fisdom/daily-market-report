# 일일 시장 리포트 (국내 코스피 / 미국 나스닥)

GitHub Actions가 평일 매일 아침 데이터를 수집 → `index.html` 생성 → GitHub Pages가 게시.

## 리포트 구성
- 한 페이지에 **🇰🇷 국내 코스피시장 / 🇺🇸 미국 나스닥시장** 두 카드 + **최근 1개월 추이 차트** (KOSPI vs 나스닥, 시작일=100 정규화)
- 등락률은 **전일(직전 영업일) 대비**
- 거래량/시총 상위 3 종목: 현재가 + 등락률 표시 (국내는 ETF·ETN 제외 일반주만)
- 국고채 3년·CD 91일(단기) 금리, 원/달러 환율, VIX(설명 포함), 미 국채 3개월·10년, 달러인덱스
  - ※ 한국 단기 금리는 국고채 1년물이 네이버에 없어 CD 91일물로 표시
- **교차 검증**: 코스피·나스닥·환율을 yfinance ↔ 네이버 두 소스에서 대조.
  통과 시 ✅ 배너, 오차(0.5%) 초과 시 해당 항목에 ⚠️ 표시 후 그대로 게시
- **🎯 보유종목 목표 매도가 추적**: 사용자의 Google Sheet(공개 링크, 읽기 전용)에서
  보유 lot과 목표 매도가를 읽어 전일 종가 기준 달성률 표시. 목표가 도달 시 상단 배너 강조.
  - 시트 규칙: A열 텍스트 = 종목 블록 / **2026년 이후** 거래만 / **매도예정가(I열) 빈칸인 행 무시**
    / 같은 인격이 이후 매도한 lot은 자동 제외
  - 시세: 미국 티커·비트코인(BTC-KRW)은 yfinance, 국내는 네이버. 새 종목은
    `market_report.py`의 `TICKER_MAP`에 추가 (미등록 시 네이버 자동완성으로 탐색 시도)

## 데이터 소스
- 미국·지수·환율: yfinance (`^KS11`, `^IXIC`, `KRW=X`, `^VIX`, `^TNX`, `DX-Y.NYB`)
- 국내 상위 종목·국고채·교차검증: 네이버 금융 공개 API
- ※ 2025-12-27부터 KRX 정보데이터시스템이 로그인 필수(KRX Data Marketplace)로 바뀌어
  pykrx를 사용하지 않습니다. VKOSPI(국내 공포지수)는 무료 공개 소스가 없어 제외했습니다.
  KRX 계정을 만들면 pykrx + `KRX_ID`/`KRX_PW` 환경변수로 VKOSPI 등을 복원할 수 있습니다.

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
