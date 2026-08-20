# Design — 청약 알리미 · 관보(官報) 에디토리얼

이 앱의 잠긴 디자인 시스템. 모든 페이지 리스타일링은 코드를 내보내기 전에 이 파일을
읽는다. 페이지마다 재생성하지 말 것 — 시스템이 자라야 하면 이 파일을 먼저 수정한다.
(hallmark redesign 다중 페이지 플로우 · 2026-08-20 · run: design-restyle)

컨셉: **관보/공문서의 종이 질감을 가진 한국형 에디토리얼**. 청약 공고라는 콘텐츠 자체가
관보(官報)다 — UI가 그 성격을 입는다. 카드 상자 더미가 아니라 헤어라인 룰과 타이포로
위계를 세운 지면(紙面). 화려한 모션 대신 인쇄물의 확신.

## Genre
editorial

## Macrostructure family
- App 페이지(대시보드·상세·북마크): **장부(ledger) 리듬** — 수평 헤어라인 룰로 행을 구분,
  카드 테두리·그림자 최소화. enrichment 금지, 기능이 지면을 끌고 간다.
- Form 페이지(로그인·가입·온보딩·프로필): **문서 양식(서식) 리듬** — 좁은 단(≤480px),
  라벨 소형캡스 스타일, 입력은 1px 룰 테두리(radius --r-md) + focus 시 남록 테두리
  (input은 전 페이지 공유 컴포넌트라 밑줄-only로 바꾸지 않는다).
- 지도는 유일한 "도판(圖版)" — 지면 위 삽입 도판처럼 가는 룰 테두리로 감싼다.

## Theme — 색 (OKLCH, 앵커 hue 85 웜 크림)
- `--color-paper`    oklch(97.5% 0.012 85)   /* 종이 — 현 크림보다 살짝 가라앉힘 */
- `--color-paper-2`  oklch(95%   0.014 85)   /* 소프트 면 (배너·푸터·hover) */
- `--color-paper-3`  oklch(92%   0.014 85)   /* 강한 면 (지도 placeholder 등) */
- `--color-ink`      oklch(22%   0.012 70)   /* 제목·강조 — 순흑 금지 */
- `--color-body`     oklch(32%   0.010 70)   /* 본문 */
- `--color-muted`    oklch(48%   0.008 70)   /* 보조 */
- `--color-faint`    oklch(62%   0.006 70)   /* 뮤트 소프트 */
- `--color-rule`     oklch(84%   0.010 85)   /* 헤어라인 룰 */
- `--color-rule-strong` oklch(30% 0.012 70)  /* 굵은 룰(제호 아래 double rule 등) */
- `--color-accent`   oklch(40%  0.065 195)   /* 남록(藍綠) — 링크·활성·1순위. 뷰포트 5% 이하 */
- `--color-accent-soft` oklch(93% 0.02 195)  /* 남록 소프트 배경(태그) */
- `--color-signal`   oklch(55%  0.19  35)    /* 주홍(朱紅) — 마감 임박·오류. 도장/인주의 색 */
- `--color-signal-soft` oklch(95% 0.03 35)
- `--color-warn`     oklch(62%  0.13  75)    /* 황토 — D-7 이내 */
- `--color-ok`       oklch(55%  0.10 165)    /* 진행중/여유 */
- `--color-focus`    oklch(40%  0.065 195)   /* 포커스 링 = 남록, 2px, 등장 애니메이션 금지 */
- peach(#ffb084) 계열은 **폐지**. 기존 --accent-peach 사용처는 paper-2/signal-soft로 이관.

## Typography (2+1 규칙)
- Display: **"Hahmlet"** (Google Fonts, variable) weight 600–700, style normal —
  h1·h2·브랜드 제호·섹션 제목. 이탤릭 헤더 금지.
- Body: **"IBM Plex Sans KR"** (Google Fonts) weight 400, 강조 700 —
  본문·UI·버튼·폼. 시스템 폴백: "Apple SD Gothic Neo", sans-serif.
- Outlier: **"IBM Plex Mono"** weight 600 — 역할 고정: **핵심 수치**(D-day figure,
  가점 점수, 마커 수치)에만. 그 외 사용 금지. `font-variant-numeric: tabular-nums`.
- 로딩: Google Fonts `<link>` + `font-display: swap` + preconnect. 서브셋 한글 woff2.
- Scale(1.25 major third, 16px 기준): --text-sm 13px · --text-base 15.5px ·
  --text-md 17px · --text-lg 21px · --text-xl 26px · --text-2xl 33px.
  h1 = --text-2xl(모바일 26px), h2 = --text-lg. display 트래킹 -0.02em.
- 본문 line-height 1.65, display 1.2. `word-break: keep-all` 유지.

## 레이아웃 · 룰(rule) 언어
- 라운드 축소: --r-md 12→6px, --r-lg 16→8px. 칩/뱃지는 pill 유지 가능하되 그라데이션 금지.
- **카드 → 지면 행**: .card는 배경 없는(종이색 그대로) 블록 + 하단 헤어라인 룰이 기본.
  테두리 4면 상자·그림자·hover lift 폐지. hover는 paper-2 배경 스왑만.
- 섹션 제목(h2) 아래는 double rule(굵은 룰 + 가는 룰) — 관보 제호 관습.
- topbar: 제호(Hahmlet 700) + 얇은 날짜 행, 아래 double rule. 내비는 텍스트 링크 + 활성 밑줄.
- 표(.table)는 이 시스템의 일급 시민 — 룰 위계(굵은 머리 룰, 가는 행 룰)로 정돈.
- 여백: 기존 4pt 스케일 유지(--s-*). 섹션 간 간격은 룰이 있으니 과감히 줄여도 된다.

## Motion
- Easing: `cubic-bezier(0.16, 1, 0.3, 1)` = `--ease-out` 단일.
- Duration: `--dur-fast 120ms` · `--dur-base 180ms`. 이 둘뿐.
- 허용: opacity/색/배경 전환, 토스트 fade+8px slide. **금지**: transform lift·bounce·
  스크롤 리빌·카운터 애니메이션. 지도 마커 선택 강조는 scale 대신 룰 강조(테두리·z-index).
- `prefers-reduced-motion: reduce` → 모든 전환 ≤150ms opacity만.

## Microinteractions stance
- silent success — 축하 토스트 금지, 실패만 토스트로 알린다(기존 관습 유지).
- hover 배경 스왑 120ms · :focus-visible 링 즉시(0ms, 애니메이션 금지).
- 낙관적 갱신 유지(북마크 토글 기존 로직).

## CTA voice
- Primary: ink 채움(oklch ink) + paper 글자, radius 6px, 44px 높이 — 도장 같은 확신.
- Secondary: 1px ink 테두리, 투명 배경, hover 시 paper-2.
- 텍스트 링크: 남록 + 밑줄(1px, offset 3px). 화살표 아이콘은 유지.

## Per-page allowances
- 모든 페이지: enrichment 금지(타이포·룰만). 지도가 유일한 도판.
- 온보딩: step 표시는 관보식 "제1장/제2장/제3장" 넘버링 허용(진짜 순서형 콘텐츠).

## What pages MUST share
- 제호 타이포(Hahmlet 700 "청약 알리미"), double rule 관습.
- 3색 역할: 남록=정보/활성, 주홍=마감/오류, 잉크=본문. 수치=Plex Mono.
- CTA voice, 룰 언어(카드 상자 대신 헤어라인), 모션 스탠스.

## What pages MAY differ on
- 단 구성(대시보드 2단 지도+목록 / 서식 페이지 1단 좁은 단).
- 표 vs 행 리스트 선택(콘텐츠 형태에 따라).

## 불변 계약 (리스타일링이 절대 깨면 안 되는 것)
- JS 소비 계약: `.card` `.chip` `.bookmark-btn` `.mk` 계열 클래스명과 모든 `data-*`
  속성명(dday/housing/pblanc/agency/area/secd/specials/rank/inarea/interest/preferred/
  lat/lng/adres/title/ftype/fval) 유지.
- aria 계약(aria-pressed/current/describedby/invalid, role), 44px 터치 타깃.
- Jinja 블록 구조(base의 title/head/topbar/content/footer/scripts)와 매크로 시그니처
  (notice_card(it), password_field(...), field(...), check(...), step_nav(...)) 유지.
- 기존 pytest 템플릿 테스트 전량 통과.

## Exports — tokens.css (base.html :root에 인라인)
```css
:root {
  --color-paper: oklch(97.5% 0.012 85); --color-paper-2: oklch(95% 0.014 85);
  --color-paper-3: oklch(92% 0.014 85);
  --color-ink: oklch(22% 0.012 70); --color-body: oklch(32% 0.010 70);
  --color-muted: oklch(48% 0.008 70); --color-faint: oklch(62% 0.006 70);
  --color-rule: oklch(84% 0.010 85); --color-rule-strong: oklch(30% 0.012 70);
  --color-accent: oklch(40% 0.065 195); --color-accent-soft: oklch(93% 0.02 195);
  --color-signal: oklch(55% 0.19 35); --color-signal-soft: oklch(95% 0.03 35);
  --color-warn: oklch(62% 0.13 75); --color-ok: oklch(55% 0.10 165);
  --color-focus: oklch(40% 0.065 195);
  --font-display: "Hahmlet", "Apple SD Gothic Neo", serif;
  --font-body: "IBM Plex Sans KR", -apple-system, "Apple SD Gothic Neo", sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;
  --text-sm: 13px; --text-base: 15.5px; --text-md: 17px;
  --text-lg: 21px; --text-xl: 26px; --text-2xl: 33px;
  --r-xs: 3px; --r-sm: 4px; --r-md: 6px; --r-lg: 8px; --r-pill: 9999px;
  --s-xxs: 4px; --s-xs: 8px; --s-sm: 12px; --s-md: 16px; --s-lg: 24px;
  --s-xl: 32px; --s-xxl: 48px;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --dur-fast: 120ms; --dur-base: 180ms;
}
```
기존 토큰명(--canvas·--ink·--accent-teal 등)에서 위 이름으로 옮기되, 전 템플릿의
참조를 함께 갱신한다(별칭 이중화 금지 — t1이 base/_macros, 각 태스크가 자기 페이지 몫).
