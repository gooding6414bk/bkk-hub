# 기발해 매일 홍보 콘텐츠 — 블로그 글 + 유튜브 쇼츠

부모님 사이트(**기발해 풍수·택일컨설팅**, gibalhae.com)를 알리는 글과 영상을 **매일 한 소재로** 만든다.
얼굴·목소리·실명 없이: 글은 문단형 정보글, 영상은 AI 음성 + 도면 그래픽 + 자막. 채널 운영 계획 v2(2026-09-15)의 파이프라인 부분을 그대로 코드로 옮긴 것이다.

```
아침 (예약 작업)        주인                     승인 뒤 (에이전트)
daily.py new   ───▶  초안 둘 읽고 "승인"  ───▶  build: 음성 → 그래픽 → mp4
blog-writer            / "쇼츠만 승인"             publish: 유튜브 예약 업로드
shorts-producer        / "반려: 이유"                       블로그 붙여넣기 묶음(네이버) 또는 워드프레스 바로
check.py                                           publisher 가 주소 기록·커밋
```

승인 전에는 아무것도 만들지도 올리지도 않는다. 상태는 `content/<날짜>/{blog.md,shorts.md}` 머리말 `status` 하나로 본다:
`draft → approved → built(쇼츠) → published`, 반려는 `rejected`.

## 대시보드 (통합 보드 bkk hub → 🎬 기발해)

통합 보드(이 저장소, Ubuntu-1 서버 포트 8020) 상단 **🎬 기발해** 를 누르면 `/gibalhae` 콘텐츠 관리로 간다. 원래 건우아비 보드 안에 있던 것을 주식 보드와 합쳐 따로 뺐다(2026-11-08). 대시보드 첫 화면에도 할 일 카드가 뜬다.
서버에서는 콘텐츠를 git 밖 `instance/gibalhae/content/` 에 두고(`GIBALHAE_CONTENT`), 저장소에 새로 올라온 초안만 자동 업데이트 때 복사해 온다. 그래서 서버에서 한 승인·수정·영상이 업데이트로 지워지지 않는다.
통합 보드는 `bkkstock/web/templates/gibalhae.html`(보드 메뉴 안에 넣은 판)을 쓰고, 스크립트·CSS는 `ui/static/` 것을 그대로 쓴다.

| 탭 | 하는 일 |
|---|---|
| 콘텐츠 | 날짜별 쇼츠·블로그 상태. 날짜를 누르면 대본·글 보기·고치기, **승인 / 쇼츠만 / 블로그만 / 반려(사유)**, **영상 만들기**(JSON2Video), 영상 미리보기·내려받기, **유튜브 예약 업로드**, 블로그 묶음(네이버 붙여넣기)·워드프레스 올리기, 올린 주소 기록. 「새 날짜 만들기」로 소재를 골라 폴더만 먼저 만들 수도 있다 |
| 연결 | API 키 넣기·**확인** — JSON2Video(남은 크레딧), 유튜브 토큰(채널 이름), Pexels, ElevenLabs(선택), 워드프레스(선택). 값은 `instance/gibalhae/keys.json`(권한 600, git 무시)에만 저장되고 화면엔 끝 4자리만 |
| 작업 | 제작·올리기 등 실행 기록과 로그(2초마다 갱신) |

초안 쓰기(글·대본)는 여기서 하지 않는다 — 클로드가 쓴다(아침 예약 작업 또는 `/daily-content draft`). 대시보드는 그 뒤의 승인·제작·올리기·확인 자리다.
코드: `pipeline/webui.py`(Flask 블루프린트, 어느 앱에나 `register_blueprint(bp)`) · 화면 `ui/gibalhae.html` · `ui/static/gibalhae.js·css` · 키 `pipeline/keys.py` · 혼자 띄우기 `app.py`(GIBALHAE_PASSWORD).

## 사람이 쓰는 말 (클로드 세션에서)

| 말 | 일어나는 일 |
|---|---|
| `/daily-content draft` 또는 "오늘 초안" | 소재 뽑고 글·대본 초안 → 검수 → 커밋 → 요약 보여 줌 |
| "승인" | 영상 제작 → 유튜브 18:00 예약 업로드 → 블로그 묶음 → 보고 |
| "쇼츠만 승인" / "블로그만 승인" | 그쪽만 |
| "반려: 둘째 장면이 길어" | 반려 기록 → 다시 써서 보여 줌 |
| "제목 2안으로 승인" | 고치고 → 승인 → 제작·업로드 |
| "블로그 올렸어 https://blog.naver.com/…" | 주소 기록(published) |
| `/daily-content status` | 날짜별 상태표 |

## 에이전트 (`.claude/agents/`)

| 이름 | 하는 일 | 안 하는 일 |
|---|---|---|
| `blog-writer` | `blog.md` — 1,500~2,500자 정보글, 이미지 지시, 태그 8 | 올리기, status 바꾸기 |
| `shorts-producer` | `shorts.md` — 45초 대본·제목 3안·설명·고정 댓글, **승인 뒤** `daily.py build` 로 mp4 | 올리기, 승인 없는 제작 |
| `publisher` | built/approved 만 `daily.py publish` — 유튜브 API 예약 업로드, 블로그 묶음/워드프레스, 주소 기록, 커밋 | 내용 수정, 승인 없는 업로드 |

오케스트레이션은 스킬 `.claude/skills/daily-content/SKILL.md`. 규칙은 `brand.md`(모든 에이전트가 먼저 읽는다), 소재는 `topics.md`.

## 파이프라인 (`pipeline/`)

| 파일 | 역할 |
|---|---|
| `daily.py` | 상태 기계 CLI — `new · status · check · approve · reject · build · publish · published`. `build` 는 기본 JSON2Video, `--engine local` 이면 아래 예비 경로 |
| `render_json2video.py` | **기본 렌더.** 대본 → JSON2Video movie.json(장면 카드 HTML · 배경 영상 · ElevenLabs/Azure 음성 · 한국어 자막 단어 강조) → 보내고 기다려 mp4 받기 |
| `html_scenes.py` | 장면 지시 → HTML/SVG 카드 (JSON2Video 가 1080×1920 으로 찍는다) |
| `check.py` | 금지 표현·신분 노출·길이 검수 (FAIL 이면 build/publish 가 멈춘다) |
| `tts.py` | (예비) 대본 → edge-tts 음성(장면별) + 자막 srt + `timing.json` |
| `graphics.py` | 장면 지시 → PNG. 카드·도면·달력·해법·단어·마무리. 블로그용 1200×800 도 같은 그림 |
| `render.py` | 장면 PNG + 음성 → 1080×1920 mp4. 장면마다 느린 줌, 0.35초 페이드, 읽는 단어만 붉게 켜지는 자막. `build/background.mp4` 가 있으면 어둡게 깔아 준다 |
| `fetch_background.py` | Pexels(무료, `PEXELS_API_KEY`)에서 얼굴 없는 세로 클립을 받아 `build/background.mp4` 로 |
| `upload_youtube.py` | YouTube Data API v3 — 제목·설명·태그·예약 시각·AI 합성 표시, 고정 댓글 |
| `youtube_auth.py` | 내 PC 에서 한 번: 구글 로그인 → 토큰 JSON |
| `publish_blog.py` | 네이버 붙여넣기 묶음(post.txt · NN.png · CHECKLIST.md) 또는 워드프레스 REST 업로드 |
| `fonts/` | Noto Sans KR (OFL) |

설치: `pip install -r gibalhae/pipeline/requirements.txt` + FFmpeg (`winget install Gyan.FFmpeg` / `apt install ffmpeg`).
실측(edge-tts): 나레이션 193자(공백 제외) → 58초. **130~175자**가 쇼츠 길이다. JSON2Video 음성은 결과 `duration` 으로 확인한다.

대본 형식과 장면 지시는 `pipeline/common.py` · `pipeline/graphics.py` 머리 설명, 예시는 `content/_example/`.

## 연결 — 무엇을 어디에 넣나

이 저장소 안의 스크립트가 공식 API 를 직접 부른다. 키는 **대시보드 → 연결** 탭에 넣는 게 기본(서버·PC). 클라우드 세션은 환경의 네트워크 비밀값(프록시 주입) 또는 환경변수. 채팅에 붙이지 않는다.
읽는 순서: `instance/gibalhae/keys.json` → 환경변수 → 프록시.

| 채널 | 방법 | 넣을 것 | 비고 |
|---|---|---|---|
| 영상 렌더 | **JSON2Video** API | 네트워크 비밀값 (api.json2video.com, 헤더 x-api-key) — 이미 등록 | 무료 600크레딧(=600초, 워터마크·비상업). 채널 운영은 Hobby 플랜(월 3,000크레딧 = 쇼츠 60편). 음성·자막은 0크레딧 |
| 유튜브 쇼츠 | YouTube Data API v3 (공식, 무료) | `YT_TOKEN_JSON` — `youtube_auth.py` 가 찍어 준 한 줄 | 구글 클라우드 프로젝트 + OAuth 데스크톱 앱, 10분. 하루 업로드 6개 안 |
| 네이버 블로그 | 공개 API 없음 → 붙여넣기 묶음 | 없음 | 자동 글쓰기 프로그램은 제재 대상. 5분 손으로 |
| 워드프레스(.com 포함) | REST API | `WP_URL` `WP_USER` `WP_APP_PASSWORD`, `BLOG_TARGET=wordpress` | 네이버 대신/병행할 때. claude.ai 커넥터 'WordPress.com' 도 있다 |
| 인스타 릴스 | Meta Graph API | (아직 없음) | 같은 mp4 재업로드. 비즈니스 계정 + 공개 URL 필요 — 다음 단계 |
| 배경 영상 | Pexels API (무료) | `PEXELS_API_KEY` | 있으면 `fetch_background.py` 가 자동으로 받는다 |
| 음성 고급 | ElevenLabs API | `ELEVENLABS_API_KEY` (아직 안 씀) | edge-tts 반응 확인 뒤 교체 자리 |

MCP 커넥터로 되는 것: WordPress.com(글 올리기), Canva(디자인), HyperFrames by HeyGen(HTML 모션 그래픽 렌더), ElevenLabs(음성 에이전트 쪽이라 TTS 용도는 API 키가 낫다).
유튜브·네이버·인스타·당근 커넥터는 없다 — 그래서 스크립트다.

## 폴더

```
gibalhae/
  brand.md            공통 브리프 (누구·말투·금지·글 구조·대본 구조)
  topics.md           소재 큐 (daily.py new 가 '쓴 날' 비어 있는 첫 줄을 뽑는다)
  content/
    _example/         완성 예시 (침대 머리 창문 쪽)
    2026-10-24/       그날 blog.md · shorts.md · build/(scene_NN.png · narration.srt · shorts.mp4 · blog/ · publish.json)
    transcripts/      토요일 부모님 답변 전사문 (비공개 원천) — 있으면 그 주 글·대본이 여기서 나온다
  pipeline/           위 표
  secrets/            youtube_token.json (git 무시)
```
mp3·mp4 는 git 에 올리지 않는다(다시 만들 수 있다). 토큰은 절대 올리지 않는다.

## 매일 돌리는 예약 작업

클라우드 예약 작업 "기발해 아침 초안" 이 매일 **06:53 KST** 새 세션을 열어 `/daily-content draft` 를 돌리고 휴대폰 알림을 보낸다.
그 세션을 열어 "승인" 이라고 답하면 나머지가 돈다. 멈추려면 claude.ai 예약 작업 목록에서 끄면 된다.
