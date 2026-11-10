# bkk hub — 📈 주식 + 🎬 기발해 통합 보드

> 다른 채팅에서 넘어온 진행 상태·키 상태·주인 규칙: [HANDOFF.md](HANDOFF.md)

로그인 하나로 두 가지를 관리하는 개인용 보드입니다. **Ubuntu-1 서버(Lightsail 서울, 2코어·2GB, 52.79.134.12)를 갭 차익거래 봇과 같이 쓰고, 보드는 따로**입니다. 건우아비 보드는 다른 서버에 있습니다.

| | 갭 차익거래 (같은 서버) | **bkk hub (이 저장소)** |
|---|---|---|
| 하는 일 | 갭 봇·갭 보드 | 주식, 기발해 콘텐츠 |
| 폴더 | `~/bkk-gap` | `~/bkk-hub` |
| 주소 | `https://gap-52-79-134-12.sslip.io` | `127.0.0.1:8020` (아래 '보드 접속') |
| 서비스 | 갭 서비스 | `bkk-hub-web`, `bkk-stock-*` 타이머, `bkk-hub-update` |
| 텔레그램 | 갭 봇 | 주식 전용 봇 (따로) |

**📈 주식** — 한국 주식(코스피·코스닥, KRX+넥스트레이드) 데이터를 매일 모으고, 보유·관심 종목의 위험을 알립니다.

- **대시보드**: 수집 상태, 내 종목 위험 표시, 재료 없는 급등(경주마), 최근 위험 공시, 기발해 할 일
- **주식 관리**: 보유·관심 종목 등록, 종목별 차트·수급·공시·뉴스, 참고 점수와 근거
- **API 관리**: 키 상태(끝 4자리만 표시), 연결 테스트, 키 변경, 발급 안내
- **운영**: 일별 수집 기록, 복구 불가 데이터 연속 수집 일수, 백업, 참고 점수 검증

주문 기능은 없습니다. 한국투자증권 키는 조회에만 씁니다.

**🎬 기발해** — 부모님 사이트(기발해 풍수·택일컨설팅) 홍보용 블로그 글·유튜브 쇼츠의 승인·제작·올리기. 원래 건우아비 보드 안(🎬)에 있던 것을 여기로 옮겼습니다. 자세한 흐름은 `gibalhae/README.md`.
- 초안은 아침 예약 작업(클로드)이 `gibalhae-daily` 브랜치의 `gibalhae/content/<날짜>/`에 커밋합니다. 코드(main)와 섞이지 않습니다.
- 서버는 10분마다 그 브랜치의 초안 폴더만 읽어 `instance/gibalhae/content/`로 가져옵니다. 서버에서 한 승인·수정·영상은 git 밖에 있어서 업데이트로 지워지지 않습니다.
- 서버에서 손대지 않은 파일은 저장소 쪽 변경(예: 채팅으로 「승인」)을 따라갑니다. 서버에서 고친 파일은 덮어쓰지 않습니다. 그래서 승인은 보드와 채팅 중 한 곳에서만 하는 게 헷갈리지 않습니다.
- 연결 키(JSON2Video, 유튜브, Pexels …)는 기발해 → 연결 탭에서 넣고, `instance/gibalhae/keys.json`(권한 600)에 저장됩니다.

## 참고 점수에 대해

내일 오를 확률이 아닙니다. 앞으로 1~3개월 동안 시장 대비 나을 쪽인지에 대한 기울기입니다.
근거가 있는 신호만 씁니다: 단기 반전, 복권형 종목(MAX), 변동성, 외국인·기관 수급, 회피 엔진의 위험 표시.
가중치는 `bkkstock/analysis/outlook.py`에 고정돼 있고 화면에서 바꿀 수 없습니다.
매일 저장해서 운영 탭에서 실제 성과와 비교합니다. 검증을 통과하기 전에는 참고로만 쓰세요.

## 서버 설치 (Lightsail Ubuntu, 코인 봇과 같은 서버)

```bash
cd ~
git clone https://github.com/<계정>/bkk-hub.git
cd bkk-hub
bash deploy/install.sh
```

설치 스크립트가 하는 일
1. 가상환경 `.venv`와 라이브러리 설치
2. `.env` 생성(권한 600), 대시보드 비밀번호 자동 생성 후 화면에 한 번 표시
3. 테스트 실행
4. systemd 서비스 1개(통합 보드 `bkk-hub-web`)와 타이머 6개(주식 5개, 자동 업데이트 1개) 설치
5. 저장소의 기발해 초안을 서버 데이터 폴더로 처음 복사

그다음
```bash
nano ~/bkk-hub/.env                                        # 키 입력
~/bkk-hub/.venv/bin/python -m bkkstock first-week-check    # 키·응답 형식 점검
nice -n 15 ~/bkk-hub/.venv/bin/python -m bkkstock backfill --days 90   # 과거 시세 (몇 분)
```

## 대시보드 접속

대시보드는 서버 안(127.0.0.1:8020)에서만 열려 있습니다. 밖에서 보는 방법은 두 가지입니다.

**SSH 터널 (가장 간단, 키 변경 가능)**
```bash
ssh -i <Lightsail 키.pem> -L 8020:localhost:8020 ubuntu@<서버 고정 IP>
# 브라우저에서 http://localhost:8020
```

**HTTPS 주소 (휴대폰에서 보기)** — 갭 보드가 이미 `gap-52-79-134-12.sslip.io`로 HTTPS를 쓰고 있으니, 같은 리버스 프록시에 주소 하나를 더 붙입니다. sslip.io는 IP가 들어간 이름이면 따로 도메인을 살 필요가 없습니다.

갭 보드는 Caddy를 씁니다. 아래 한 줄이면 Caddyfile을 백업하고 블록을 덧붙인 뒤 검사·다시 읽기까지 합니다.
```bash
bash ~/bkk-hub/deploy/https.sh        # → https://hub-52-79-134-12.sslip.io
```
직접 넣으려면 Caddyfile에 아래 블록을 추가 후 `sudo systemctl reload caddy`:
```
hub-52-79-134-12.sslip.io {
    reverse_proxy 127.0.0.1:8020
}
```
nginx:
```
location / {
    proxy_pass http://127.0.0.1:8020;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```
HTTP(암호화 없음)로 들어오면 API 관리 화면의 키 입력은 잠깁니다.

## 자동 일정 (한국 시간)

| 시각 | 작업 |
|---|---|
| 07:30 | 토큰 재발급, 밤사이 공시, 전 거래일 실패 항목 재시도 |
| 평일 08:00~20:50, 10분마다 | 새 공시, 처음 본 시각 기록, 보유 종목 위험 공시 즉시 알림 |
| 평일 20:30 | 일일 수집 |
| 평일 21:00 | 텔레그램 일일 보고, 백업 |
| 평일 21:40 | 감시 (수집이 안 돌았으면 알림) |
| 10분마다 | 새 커밋이 있으면 자동 업데이트. 이 보드만 재시작, 실패하면 되돌림. 기발해 제작 중이면 미룸. 새 기발해 초안 복사 |

모든 작업은 낮은 우선순위(nice 15), CPU 70%, 메모리 700MB 상한으로 돕니다. 코인 봇이 우선입니다.

## 수집하는 것

| 항목 | 출처 | 놓치면 |
|---|---|---|
| 상장 종목, 거래정지·관리종목·시장경보 | 한국투자증권 종목 마스터 파일 (공개) | **복구 불가** |
| 넥스트레이드 거래 종목과 시세 | 한국투자증권 (NX) | **복구 불가** |
| 공시와 처음 본 시각 | OpenDART (10분마다) | **시각은 복구 불가** |
| 코스피·코스닥 전 종목 일별 시세, 지수 | KRX Open API | 다음 날 아침 자동 재시도 |
| 종목별 투자자 매매 (KRX+NXT 통합) | 한국투자증권 (UN) | 다음 날 아침 자동 재시도 |
| 관심 종목 공매도·신용잔고 | 한국투자증권 | 다음 날 아침 자동 재시도 |

## 데이터

```
data/
  raw/YYYY-MM-DD/   API 응답 원본 (gzip). 한 번 쓰면 고치지 않는다
  clean/<표>/YYYY-MM-DD.parquet   분석용. 같은 날 다시 받으면 .v2로 추가
  ops.sqlite        수집 기록, 공시, 관심 종목, 알림, API 상태
  backups/          매일 운영 DB와 원본 묶음, 14일 보관
  secrets/          한국투자증권 토큰 캐시 (권한 700)
instance/
  gibalhae/content/ 서버의 기발해 콘텐츠 (승인·수정·영상). git 밖
  gibalhae/keys.json  기발해 연결 키 (권한 600)
```

서버가 망가지면 복구 불가 데이터도 사라집니다. `.env`의 `BACKUP_CMD`로 서버 밖 복사를 켜거나 Lightsail 자동 스냅샷을 켜세요.

## 비밀번호 바꾸기

```bash
cd ~/bkk-hub && .venv/bin/python -m bkkstock set-password && sudo systemctl restart bkk-hub-web
```

## 명령

```bash
.venv/bin/python -m bkkstock daily [--day 2026-11-06] [--force]
.venv/bin/python -m bkkstock dart-poll | morning | report | watchdog
.venv/bin/python -m bkkstock backfill --days 90
.venv/bin/python -m bkkstock first-week-check
journalctl -u bkk-stock@daily -n 50          # 일일 수집 로그
systemctl list-timers 'bkk-stock*'           # 다음 실행 시각
```

## 갭 차익거래 봇과 같은 서버를 쓰는 법

- 모든 주식 작업은 nice 15, CPU 70%, 메모리 700MB 상한. 통합 보드는 메모리 900MB 상한(기발해 제작 포함).
- 자동 업데이트는 이 저장소만 받고 `bkk-hub-web`만 재시작합니다.
- 텔레그램은 주식 전용 봇을 따로 씁니다. 같은 토큰을 쓰면 충돌 오류가 납니다.
- 서버 사양(2코어, 2GB RAM)에서 일일 수집은 몇 분입니다. 메모리가 빠듯하면 `.env`의 `INVESTOR_SCOPE=watchlist`로 줄이세요.
- Lightsail은 CPU 크레딧이 바닥나면 느려집니다(갭 봇이 11-03에 겪음). 기발해 영상은 JSON2Video 서버에서 만들어서 이 서버 CPU를 거의 안 씁니다. 로컬 렌더(`--engine local`, ffmpeg)는 이 서버에서 돌리지 마세요.

## 화면 미리보기 (키 없이)

```bash
BKK_STOCK_DATA=data-demo python -m bkkstock demo-seed
BKK_STOCK_DATA=data-demo BKK_STOCK_DEMO=1 DASH_PASSWORD=demo python -m bkkstock web
```
가상의 회사와 가격으로 채운 샘플 화면이 뜹니다.

## 첫 주에 확인할 것

`first-week-check`가 아래를 점검합니다.
- 키마다 호출 성공
- KRX 응답 필드 이름이 코드의 예상과 같은지 (`BAS_DD`, `ISU_CD`, `TDD_CLSPRC` …)
- OpenDART 공시검색 응답에 접수 시각 필드가 있는지
- 한국투자증권 통합(UN) 거래량 = KRX(J) + NXT(NX) 인지
- KRX 과거 데이터에 상장폐지 종목이 들어 있는지

확인하지 못한 채로 코드에 들어간 가정이 몇 개 있습니다. 첫 주 점검에서 다르게 나오면 그 부분만 고치면 됩니다.
- KRX Open API 응답 필드 이름과 인증키 전달 방식 (헤더 실패 시 쿼리로 자동 재시도)
- KRX 그날 데이터가 20:30에 올라와 있는지 (없으면 다음 날 아침 자동 재시도)
- 한국투자증권 수급 금액 단위(백만 원으로 가정)
