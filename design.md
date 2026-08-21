# Design — 청약 알리미 · 맵 프로덕트 v2

이 앱의 잠긴 디자인 시스템. 모든 페이지 리스타일링은 코드를 내보내기 전에 이 파일을
읽는다. 페이지마다 재생성하지 말 것 — 시스템이 자라야 하면 이 파일을 먼저 수정한다.
(t1-system · 2026-08-21 · run: designv2 · v1 "관보 에디토리얼" 전면 폐기)

컨셉: **한국 프롭테크 지도 관제**의 유틸리테리언-테크니컬 톤. 데이터 밀도 높은 지도
프로덕트(다방 dabangapp.com 참고)의 정밀함 — "clean & modern"이라는 무정향 기본값이
아니라, 아래 결정들이 그 정향의 실체다. 종이 질감의 관보 은유는 폐기하고, 박스형 카드
+ 단일 코발트 액센트 + 극단 웨이트 대비 타이포로 프로덕트 UI를 세운다.

## Genre
product / map-console

## Macrostructure family
- App 페이지(대시보드·상세·북마크): **카드 그리드** — surface 배경 + 1px line 보더 +
  radius 14px 박스. hover 시 보더 강조 + shadow-2. 헤어라인 룰·장부 리듬 폐기.
- Form 페이지(로그인·가입·온보딩·프로필): 좁은 단(≤480px), 입력 높이 46px + radius
  --r-md + focus 시 accent 보더 + 3px accent-soft 링(input은 전 페이지 공유 컴포넌트).
- 지도는 유일한 "도판" — 카드와 동일한 line 보더로 감싼다.
- 헤더는 **다방식 1행 상단바**: 로고 + 검색창(디자인만) + 텍스트 내비 + 계정 액션.
  필터 드롭다운 칩(2행)은 대시보드(인덱스) 페이지 소유 — base는 토큰만 제공한다.

## Theme — 색 (OKLCH, 단일 앵커 hue 262 코발트)
전 토큰 OKLCH. 뉴트럴 전부 hue 262로 틴트(쿨 그레이) — 순흑백 금지. 액센트는
내비 활성·링크·CTA·활성 칩·1순위에만 쓴다(뷰포트 ≤3%).

- `--color-canvas`   oklch(97.5% 0.004 262)   /* 페이지 배경 */
- `--color-surface`  oklch(99.4% 0.002 262)   /* 카드·헤더 */
- `--color-surface-2` oklch(95.5% 0.006 262)  /* hover·소프트 면 */
- `--color-ink`      oklch(21%   0.02  262)   /* 제목·강조 — 순흑 금지 */
- `--color-body`     oklch(32%   0.015 262)   /* 본문 */
- `--color-muted`    oklch(48%   0.012 262)   /* 보조 */
- `--color-faint`    oklch(63%   0.01  262)   /* 뮤트 소프트 */
- `--color-line`     oklch(90%   0.006 262)   /* 카드·인풋 보더 */
- `--color-line-strong` oklch(80% 0.01 262)   /* hover 보더 */
- `--color-accent`   oklch(48%   0.19  262)   /* 코발트 — 링크·활성·CTA·1순위 */
- `--color-accent-strong` oklch(42% 0.2 262)  /* accent hover */
- `--color-accent-soft` oklch(94% 0.03 262)   /* accent 소프트 배경 */
- `--color-signal`   oklch(55%   0.19  25)    /* 웜레드 — 마감 임박·오류 */
- `--color-signal-soft` oklch(95% 0.03 25)
- `--color-warn`     oklch(62%   0.13  75)    /* 앰버 — D-7 이내 */
- `--color-warn-soft` oklch(95% 0.05 75)
- `--color-ok`       oklch(55%   0.11 165)    /* 진행중/여유 */
- `--color-ok-soft`  oklch(94%   0.05 165)
- `--color-focus`    = `--color-accent`
- 그림자(쿨 틴트, 저투명 2단): `--shadow-1` 0 1px 2px oklch(20% .02 262 / .06) ·
  `--shadow-2` 0 6px 20px oklch(20% .02 262 / .10) — 카드 hover 전용.
- 레이아웃 폭: `--w-page` 1080px(.wrap max-width).

## Typography (2+1 규칙)
- Display + Body: 단일 패밀리 **"SUIT Variable"**(jsDelivr CDN, variable) — 극단
  웨이트 대비로 위계를 만든다: 본문 400 ↔ 제목(h1/h2/.display) 800. 이탤릭 헤더 금지.
  기본값(Noto/Inter/Pretendard)과 직전 시스템의 IBM Plex Sans KR 모두 기각 —
  2차 수렴 방지를 위해 재선택하지 않는다.
- Outlier: **"IBM Plex Mono"** weight 600(Google Fonts) — 역할 고정: **핵심 수치**
  (D-day figure, 가점 점수, 마커 수치)에만. 한글 폴백은 SUIT Variable로 이어져
  임의 시스템 폰트로 새지 않는다. `font-variant-numeric: tabular-nums`.
- 로딩: SUIT Variable은 `<link rel="preconnect" href="https://cdn.jsdelivr.net">` +
  jsDelivr stylesheet 링크. IBM Plex Mono만 기존처럼 Google Fonts
  `<link>`(`display=swap`, `fonts.googleapis.com`/`fonts.gstatic.com` preconnect 2개
  유지)로 로드한다.
- Scale: --text-xs 12px · --text-sm 13px · --text-base 14.5px · --text-md 16px ·
  --text-lg 19px · --text-xl 24px(모바일 22px) · --text-2xl 32px(모바일 28px).
  h1 = --text-xl/800, h2 = --text-lg/800, .display = --text-2xl/800. 트래킹 -0.02em.
- 본문 line-height 1.6. `word-break: keep-all` 유지.

## 레이아웃 · 컴포넌트 셰이프
- 라운드: --r-xs 4px · --r-sm 6px · --r-md 10px · --r-lg 14px · --r-pill 9999px.
- **카드 복귀**: surface 배경 + 1px line 보더 + radius 14px + hover 시 보더 진해짐
  (line-strong) + shadow-2. 헤어라인 룰·double rule·장부 리듬 전면 폐기.
- 섹션 제목(h2)은 double rule 없이 margin-bottom 14px만으로 여백을 준다.
- topbar: full-width surface 1행, 1px 하단 보더, sticky top:0. 좌→우
  `[.brand][.search-bar][.topnav][.topbar-actions]`. 내비는 pill 배경 hover.
- 표(.table)는 th 배경 surface-2 + 12px/700 muted, td tabular-nums, 1px line 하단.
- 여백: 기존 4pt 스케일 유지(--s-*).

## 헤더 · 검색창(디자인만)
다방(dabangapp.com) 지도 페이지 실측 구조를 따른 1행 헤더:

```html
<header class="topbar"><div class="inner">
  <a class="brand" href="/">청약 알리미</a>
  <form class="search-bar" role="search" onsubmit="return false">
    <svg class="ic" aria-hidden="true"><use href="#i-search"></use></svg>
    <input type="search" placeholder="지역·공고명·아파트명 검색" aria-label="공고 검색">
  </form>
  <nav class="topnav"><!-- 지도 / 북마크 / (로그인 시) 내정보·로그아웃 --></nav>
  <div class="topbar-actions"><!-- 비로그인 시 로그인/회원가입 버튼 --></div>
</div></header>
```

- 검색창은 **디자인만** — `onsubmit="return false"`로 Enter를 억제하고, JS·라우트
  없음. `disabled`도 아니다(비활성 회색은 접근성·심미 모두 저해). `role="search"` +
  `aria-label="공고 검색"`으로 접근성 계약을 지킨다.
- `.search-bar`: flex:1, max-width 520px, height 42px, focus-within 시 accent 보더
  + 0 0 0 3px accent-soft 링.
- 필터 드롭다운 칩(2행)은 대시보드(인덱스) 페이지 소유 — base는 만들지 않는다.
- ≤720px: 헤더가 flex-wrap하고 `.search-bar`가 order:3 + flex-basis:100%로 2행
  전체 폭을 차지한다. overflow-x는 어떤 뷰포트에서도 발생하지 않아야 한다.

## Motion
- Easing: `cubic-bezier(0.16, 1, 0.3, 1)` = `--ease-out` 단일.
- Duration: `--dur-fast 120ms` · `--dur-base 180ms`. 이 둘뿐.
- 전환 대상은 transform/opacity/border-color/box-shadow만. **금지**: bounce·
  스크롤 리빌·카운터 애니메이션.
- `prefers-reduced-motion: reduce` → 모든 전환 사실상 끔(.01ms, animation:none).

## Microinteractions stance
- silent success — 축하 토스트 금지, 실패만 토스트로 알린다(기존 관습 유지).
- hover 전환 120ms · :focus-visible 링 즉시(0ms, 애니메이션 금지).
- 낙관적 갱신 유지(북마크 토글 기존 로직).

## CTA voice
- Primary: accent 채움 + surface(거의 흰) 글자, radius 10px, 44px 높이,
  hover는 accent-strong. ink 채움 도장 버튼(v1)은 폐기.
- Secondary: surface 배경 + 1px line 보더, hover는 surface-2.
- `.btn-sm`(height 36px, padding 8px 14px, font 13px) — 헤더 로그인/회원가입 등
  좁은 자리의 CTA 전용 신규 변형.
- 텍스트 링크: accent 컬러, 밑줄 없음(hover 시 색만 진해짐).

## 상태색 의미
signal(마감임박, 웜레드 hue 25) · warn(D-7 이내, 앰버 hue 75) · ok(여유, hue 165).
각각 `-soft` 짝 토큰 제공. 뱃지 변형:
- `.badge--soon` = signal 채움 + surface 텍스트(가장 급함 — 유일하게 채움)
- `.badge--mid` = warn-soft 배경 + warn 텍스트
- `.badge--far` = ok-soft 배경 + ok 텍스트
- `.badge--pre` = accent-soft 배경 + accent 텍스트

## Per-page allowances
- 모든 페이지: enrichment 금지(카드·타이포·컬러만). 지도가 유일한 도판.
- 온보딩: step 표시는 순서형 넘버링 허용(진짜 순서형 콘텐츠).

## What pages MUST share
- 브랜드 워드마크(SUIT Variable 800 "청약 알리미"), 카드 셰이프(line 보더+radius 14).
- 3색 역할: accent=정보/활성, signal=마감/오류, ink=본문. 수치=Plex Mono.
- CTA voice, 카드 언어(박스+hover shadow), 모션 스탠스.
- 공통 헤더 계약(`.topbar`/`.brand`/`.search-bar`/`.topnav`/`.topbar-actions`) —
  검색창은 디자인만 유지, 기능을 추가하지 않는다.

## What pages MAY differ on
- 단 구성(대시보드 2단 지도+목록 / 서식 페이지 1단 좁은 단).
- 표 vs 카드 리스트 선택(콘텐츠 형태에 따라).
- 필터 칩 2행의 구체 옵션 구성(대시보드 소유).

## 하위 호환 — 구 토큰 별칭 (v1 관보 에디토리얼 → v2)
아직 이관되지 않은 페이지가 깨진 스타일 없이 렌더되도록, base.html의 :root는 구
토큰명 전부를 v2 값으로의 별칭으로 유지한다(값 자체가 v2를 가리키므로 미마이그레이션
페이지도 자동으로 v2 색·타이포를 입는다):

```css
--color-paper: var(--color-canvas); --color-paper-2: var(--color-surface-2);
--color-paper-3: oklch(93% 0.008 262);
--color-rule: var(--color-line); --color-rule-strong: var(--color-line-strong);
--canvas: var(--color-paper); --surface-soft: var(--color-paper-2);
--surface-card: var(--color-paper-2); --surface-strong: var(--color-paper-3);
--ink: var(--color-ink); --body-strong: var(--color-ink); --body: var(--color-body);
--muted: var(--color-muted); --muted-soft: var(--color-faint);
--hairline: var(--color-rule); --hairline-soft: var(--color-rule);
--primary: var(--color-accent); --primary-active: var(--color-accent-strong);
--primary-disabled: var(--color-rule); --on-primary: var(--color-surface);
--accent-teal: var(--color-accent); --on-teal: var(--color-surface);
--accent-peach: var(--color-signal-soft); --accent-peach-soft: var(--color-paper-2);
--ok: var(--color-ok); --warn: var(--color-warn); --danger: var(--color-signal);
--font-sans: var(--font-body);
--s-section: 96px; --r-xl: 12px;
```

**다운스트림 페이지 태스크(t2-index/t3-detail/t4-forms)가 자기 페이지를 v2로
이관할 때만** 이 별칭 대신 신규 토큰명(`--color-surface`, `--color-line` 등)을
직접 참조하도록 갱신한다 — base.html은 건드리지 않는다(별칭 이중화 금지).

## 불변 계약 (리스타일링이 절대 깨면 안 되는 것)
- JS 소비 계약: `.card` `.chip` `.bookmark-btn` `.mk` 계열 클래스명과 모든 `data-*`
  속성명(dday/housing/pblanc/agency/area/secd/specials/rank/inarea/interest/preferred/
  lat/lng/adres/title/ftype/fval) 유지.
- aria 계약(aria-pressed/current/describedby/invalid, role), 44px 터치 타깃.
- Jinja 블록 구조(base의 title/head/topbar/content/footer/scripts)와 매크로 시그니처
  (notice_card(it), password_field(...), field(...), check(...), step_nav(...)) 유지.
- 아이콘 스프라이트 22개(추가/삭제 금지). 이모지 0개.
- topbar z-index(100) < toast z-index(1000). `--topbar-h`는 JS가 실측해 갱신.
- 기존 pytest 템플릿 테스트 전량 통과.

## Exports — tokens.css (base.html :root에 인라인)
```css
:root {
  --color-canvas: oklch(97.5% 0.004 262);
  --color-surface: oklch(99.4% 0.002 262);
  --color-surface-2: oklch(95.5% 0.006 262);
  --color-ink: oklch(21% 0.02 262); --color-body: oklch(32% 0.015 262);
  --color-muted: oklch(48% 0.012 262); --color-faint: oklch(63% 0.01 262);
  --color-line: oklch(90% 0.006 262); --color-line-strong: oklch(80% 0.01 262);
  --color-accent: oklch(48% 0.19 262); --color-accent-strong: oklch(42% 0.2 262);
  --color-accent-soft: oklch(94% 0.03 262);
  --color-signal: oklch(55% 0.19 25); --color-signal-soft: oklch(95% 0.03 25);
  --color-warn: oklch(62% 0.13 75); --color-warn-soft: oklch(95% 0.05 75);
  --color-ok: oklch(55% 0.11 165); --color-ok-soft: oklch(94% 0.05 165);
  --color-focus: var(--color-accent);
  --font-display: "SUIT Variable", "Apple SD Gothic Neo", sans-serif;
  --font-body: "SUIT Variable", -apple-system, "Apple SD Gothic Neo", sans-serif;
  --font-mono: "IBM Plex Mono", "SUIT Variable", ui-monospace, monospace;
  --text-xs: 12px; --text-sm: 13px; --text-base: 14.5px; --text-md: 16px;
  --text-lg: 19px; --text-xl: 24px; --text-2xl: 32px;
  --r-xs: 4px; --r-sm: 6px; --r-md: 10px; --r-lg: 14px; --r-pill: 9999px;
  --shadow-1: 0 1px 2px oklch(20% 0.02 262 / 0.06);
  --shadow-2: 0 6px 20px oklch(20% 0.02 262 / 0.10);
  --w-page: 1080px;
  --s-xxs: 4px; --s-xs: 8px; --s-sm: 12px; --s-md: 16px; --s-lg: 24px;
  --s-xl: 32px; --s-xxl: 48px;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --dur-fast: 120ms; --dur-base: 180ms;
}
```
구 토큰명(--canvas·--ink·--accent-teal 등)은 위 "하위 호환" 절의 별칭 그대로
base.html에 유지된다 — 페이지 태스크가 자기 몫을 이관할 때 신규 이름으로 옮기되,
base.html의 별칭 정의 자체는 건드리지 않는다(별칭 이중화 금지 — t1이 base/_macros,
각 태스크가 자기 페이지 몫).
