---
name: daily-content
description: 기발해(풍수·택일) 매일 홍보 콘텐츠 운영 — 아침에 블로그 글 + 유튜브 쇼츠 초안을 만들어 주인에게 보여 주고, 주인이 "승인" 하면 영상 제작 → 업로드까지. "/daily-content draft|approve|build|publish|status", "오늘 초안", "승인", "쇼츠만 승인", "반려: …", "올려" 같은 요청에 쓴다.
---

# 매일 콘텐츠 운영 — 초안 → 승인 → 제작 → 업로드

한 소재로 **블로그 글 1편 + 쇼츠 1편**. 주인(gooding6414)이 승인하기 전에는 아무것도 만들지도 올리지도 않는다.
모든 상태는 `gibalhae/content/<날짜>/{blog.md,shorts.md}` 머리말의 `status` 에 있다
(draft → approved → built(쇼츠) → published). 규칙 문서는 `gibalhae/brand.md`, 명령 설명은 `gibalhae/README.md`.

인자 없이 불리면 `python gibalhae/pipeline/daily.py status` 를 보여 주고 다음 할 일을 제안한다.

## `draft` — 아침 초안 (예약 작업이 매일 이걸 부른다)

1. `python gibalhae/pipeline/daily.py new` → 오늘 폴더와 소재(topics.md 에서 안 쓴 첫 줄). 이미 있으면 그 날짜를 쓴다.
   특정 소재를 원하면 `--topic N`, 다른 날짜면 `--date YYYY-MM-DD`.
2. 에이전트 둘을 **동시에** 띄운다 (Agent 도구, 각각 날짜를 알려 준다):
   - `shorts-producer` — "<날짜> 쇼츠 초안"
   - `blog-writer` — "<날짜> 블로그 초안" (쇼츠 대본이 먼저 끝났으면 같은 그림을 쓰라고 알려 준다)
3. `python gibalhae/pipeline/check.py <날짜>` — FAIL 이 있으면 해당 에이전트에 반려 사유와 함께 다시 시킨다.
4. 커밋·푸시: `git add gibalhae/content/<날짜> gibalhae/topics.md && git commit -m "기발해 <날짜> 초안 — <제목>" && git push -u origin HEAD`.
5. **주인에게 보여 준다** — 마지막 메시지에 반드시:
   - 쇼츠: 제목 3안 · 장면 5개 한 줄씩 · 나레이션 글자 수와 예상 초
   - 블로그: 제목 · 소제목 목록 · 첫 문단 · 글자 수 · 이미지 지시
   - 검수 결과(WARN 포함)
   - 한 줄: **"승인" 이라고 답하면 영상을 만들어 올립니다. "쇼츠만 승인" · "블로그만 승인" · "반려: 이유" · "제목 2안으로" 도 됩니다.**
   초안 단계에서는 영상을 만들지 않는다(음성 서버·시간 아낌).

## `approve` — 주인이 승인했을 때 ("승인", "ㅇㅋ", "좋아 올려")

주인의 말 그대로만 승인한다. "쇼츠만" 이면 `--shorts`, "블로그만" 이면 `--blog`.
수정 요청이 섞여 있으면("제목 2안으로", "둘째 장면 줄여") **먼저 고치고**(해당 에이전트 또는 직접 Edit) 검수를 다시 돌린 뒤 승인한다.
```
python gibalhae/pipeline/daily.py approve <날짜> [--blog|--shorts]
```
그리고 곧바로 `build` 와 `publish` 로 이어진다 — 주인은 한 번만 말한다.

## `build` — 영상 제작 (쇼츠가 approved 일 때)

`shorts-producer` 에게 "<날짜> 쇼츠 제작" 을 시킨다(또는 직접 `python gibalhae/pipeline/daily.py build <날짜>` — JSON2Video 로 렌더, 1~3분).
끝나면 `build/scene_00.png`(훅)와 마지막 장면 그림, `ffprobe` 길이를 확인한다. 59.5초 초과면 대본을 줄여 **다시 승인을 묻는다.**
주인이 영상 파일을 볼 수 있게 `gibalhae/content/<날짜>/build/shorts.mp4` 를 보낸다(SendUserFile 이 있으면 그걸로).

## `publish` — 업로드 (쇼츠 built · 블로그 approved)

`publisher` 에게 "<날짜> 올려" 를 시킨다. 유튜브는 그날 18:00 KST 예약 공개, 블로그는 네이버 붙여넣기 묶음(기본).
인증(`YT_TOKEN_JSON` 등)이 없으면 publisher 가 건너뛰고 이유를 보고한다 — 그대로 주인에게 전하고, 무엇을 환경 비밀값에 넣어야 하는지 `gibalhae/README.md` 「연결」 표로 안내한다.
끝나면 유튜브 주소·예약 시각, 블로그 묶음 경로(또는 주소), 커밋 여부를 한 번에 보고한다.

## `reject` — 반려 ("반려: 너무 길어", "다시 써")

`python gibalhae/pipeline/daily.py reject <날짜> [--blog|--shorts] --why "이유"` 뒤 해당 에이전트에 이유를 붙여 다시 쓰게 하고, 다시 보여 준다.

## 지켜야 할 것

- `status` 를 손으로 approved 로 바꾸지 않는다 — 주인의 말이 있어야 한다.
- 올리기 전엔 항상 `check.py` FAIL 0.
- 음성 서버(edge-tts)에 못 붙으면 그 사실을 보고한다. 가짜 결과를 만들지 않는다.
- 토큰·비밀번호를 채팅으로 요구하지 않는다.
- 한 날짜에 쇼츠 하나·블로그 하나. 둘째 편이 필요하면 다른 날짜 폴더(`--date`)를 쓴다.
