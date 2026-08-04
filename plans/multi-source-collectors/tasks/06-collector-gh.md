# Task 06: GH collector (GH주택청약·임대센터 목록 HTML 파싱 + NetFunnel 감지 중단)

## Objective
`src/collectors/gh.py`의 `fetch_gh_notices()`가 GH주택청약·임대센터 메인의 공고 카드
목록을 파싱해 통합 스키마 모델 리스트를 반환한다. 대기열(NetFunnel)이 활성화된 응답을
받으면 우회하지 않고 경고 후 빈 리스트를 반환한다.

## Wiki pages (read these first, only these)
- `wiki/backend/common/integrations/externally-owned-defaults.md` — 레포가 소유하지 않은 외부 동작이 말없이 바뀌는 경로를 감지·중단하기
- `wiki/security/input/validation-at-trust-boundaries.md` — 외부 HTML 문자열의 검증·길이 상한
- `wiki/testing/quality/minimum-case-set.md` — 케이스 선정
- `wiki/testing/data/test-data-and-isolation.md` — 고정 HTML 픽스처로 격리

## Inputs
- 대상 URL (robots.txt `Allow: /*` 확인됨 — 2026-08-04):
  `https://apply.gh.or.kr/co/coa/selectMainView.do`
  루트 `https://apply.gh.or.kr/`는 이 주소로 보내는 리다이렉트 스텁이므로 직접 이 URL을 친다.
- **실측 카드 마크업**(속성이 일부 깨져 있으니 관대한 정규식이 필요하다):
  ```html
  <li class="all">
    <a class="thbox brd-df apy-c1" href="javascript:void(0);"
    data-previewYn="N"
    data-pbancNo="801"
    data-pbancKndCd="01"
    data-bizTyNm="국민임대"
    data-bizTyCd="02"
    >
      <span class="bg-blue_mt px-3 py-2 rounded-[4px] text-white text-xs" rentalhouse ">
      국민임대
      </span>
      <p class="leading-6 line-clamp-2 max-h-[46px] my-2">다산센트럴파크6단지 국민임대주택 예비입주자 모집공고
      </p>
      <p class="calender_box"><span class="">신청기간  ~  </span></p>
      <span class="  status-on  mt-3 py-2 py-3 text-center w-full "></span>
    </a>
  </li>
  ```
  - `data-pbancNo` = **안정적 공고번호** → `native_id`
  - `data-bizTyNm` = 공급유형(매입임대·국민임대·**상가임대** 등)
  - 공고명 = `<p class="leading-6 ...">`의 텍스트
  - `href`는 `javascript:void(0);` — **상세 URL이 없다**
  - `신청기간`은 값이 비어 있다(실측) → D16
- **NetFunnel 판별 근거**: 현재 루트 스텁의 대기열 호출은 주석 처리되어 있다
  (`// NetFunnel_Action({action_id:"service_main"}, ...)`). 재활성화되면 주석이 풀린다.
- `src/collectors/lh.py` — `ValidationError` 스킵 루프(L352~360).
- Decisions that bind you: D9, D10(타임아웃 30s), D13(표준 라이브러리만),
  D14(길이 상한), D15(NetFunnel 감지 시 우회 금지·빈 리스트), D16(접수기간 None),
  D23·D24(테스트).

## Steps
1. `src/collectors/gh.py`를 만든다. 상수:
   ```python
   GH_URL = "https://apply.gh.or.kr/co/coa/selectMainView.do"
   # 상세 URL이 목록에 없다 — 청약센터 메인을 공고 링크로 쓴다.
   GH_NOTICE_URL = GH_URL
   # 주택이 아닌 공급유형은 수집 대상이 아니다(실측: "상가임대"가 섞여 있다).
   EXCLUDED_BIZ_TY = ("상가",)
   MAX_NAME_LEN = 200
   ```
2. NetFunnel 감지 함수를 만든다(D15). **주석 처리된 호출은 활성으로 보지 않는다**:
   ```python
   _NETFUNNEL_RE = re.compile(r"^\s*(?!//)\s*NetFunnel_Action\s*\(", re.M)


   def _netfunnel_active(html_text: str) -> bool:
       """대기열(NetFunnel)이 켜졌는지 판정한다.

       켜져 있으면 GH가 트래픽을 통제하겠다는 뜻이므로 우회하지 않고 수집을 포기한다(D15).
       주석(`// NetFunnel_Action(`) 상태는 비활성으로 본다.
       """
       return bool(_NETFUNNEL_RE.search(html_text))
   ```
3. 카드 파서를 순수 함수로 만든다:
   ```python
   _CARD_RE = re.compile(r'<a\b[^>]*\bdata-pbancNo="(\d+)"[^>]*>(.*?)</a>', re.S | re.I)
   _BIZ_RE = re.compile(r'data-bizTyNm="([^"]*)"', re.I)
   _NAME_RE = re.compile(r'<p class="leading-6[^"]*">(.*?)</p>', re.S | re.I)
   _TAG_RE = re.compile(r"<[^>]+>")
   ```
   `_parse_cards(html_text) -> list[dict]`:
   - `_CARD_RE`로 카드를 모은다. 열림 태그 속성은 `<a ...>` 안에 있고 `data-bizTyNm`은
     그 열림 태그에 있으므로, `_CARD_RE`의 그룹 2(내부 HTML)가 아니라 **매치 전체
     문자열**(`m.group(0)`)에서 `_BIZ_RE`를 찾는다.
   - 공고명은 그룹 2에서 `_NAME_RE` → 태그 제거 → `html.unescape` → 공백 정규화 →
     `MAX_NAME_LEN`으로 자름.
   - 공고명이 비면 그 카드는 건너뛴다.
   - `data-bizTyNm`이 `EXCLUDED_BIZ_TY` 중 하나를 포함하면 건너뛴다.
   - 반환 dict: `{"pbanc_no", "biz_ty", "name"}`.
4. `GhNotice(BaseModel)`을 정의한다. 통합 스키마 필드 집합은 `MyhomeNotice`와 동일,
   `agency: str = "GH"` 고정. 매핑:
   - `pblanc_no` = `f"gh-{pbanc_no}"`
   - `house_nm` = 공고명 (`[정정]` 접두가 있으면 `[정정공고]`로 치환 — SH와 동일 규약)
   - `house_manage_no` = `None` (이름 기반 정정 그룹핑 경로)
   - `house_secd_nm` / `house_dtl_secd_nm` = `biz_ty`
   - `area_nm` = `"경기"` 고정 (GH는 경기도 전용 공사)
   - `bsns_mby_nm` = `"경기주택도시공사"`, `agency` = `"GH"`
   - `rcrit_pblanc_de` / `rcept_bgnde` / `rcept_endde` / `przwner_presnatn_de` = `None` (D16)
   - `rent_gtn` / `mt_rntchrg` / `tot_suply_hshldco` = `None`
   - `pblanc_url` = `GH_NOTICE_URL`
   - `raw` = `{"_gh_pbanc_no": pbanc_no, "_gh_biz_ty": biz_ty}`
5. 수집 함수:
   ```python
   def fetch_gh_notices(*, client: httpx.Client | None = None) -> list[GhNotice]:
       """GH주택청약·임대센터 메인에서 공고 목록을 수집한다.

       대기열(NetFunnel)이 켜져 있으면 우회하지 않고 빈 리스트를 반환한다(D15).
       """
   ```
   - `client or httpx.Client(timeout=30.0, follow_redirects=True)` (D10)
   - `GET GH_URL` → `raise_for_status()` → `text`
   - `if _netfunnel_active(text): logger.warning("GH 대기열(NetFunnel) 활성 — 이번 회차 수집 건너뜀"); return []`
   - `_parse_cards` → `GhNotice.model_validate` (실패는 warning 후 스킵, D9)
   - `pblanc_no` 기준 중복 제거
6. `tests/test_gh.py`를 만든다. **위 실측 마크업 그대로**를 픽스처 문자열로 쓰고
   `httpx.MockTransport`로 응답을 준다(D23). 케이스(D24):
   - `test_parses_real_card`: `pblanc_no == "gh-801"`, `house_nm`이
     `"다산센트럴파크6단지"`로 시작, `house_secd_nm == "국민임대"`,
     `area_nm == "경기"`, `agency == "GH"`, `rcept_endde is None`.
   - `test_excludes_commercial_lease`: `data-bizTyNm="상가임대"` 카드는 결과에 없다.
   - `test_netfunnel_active_returns_empty`: 본문에 주석 아닌
     `NetFunnel_Action({action_id:"service_main"});`가 있으면 `[]`이고 경고 로그가 남는다(`caplog`).
   - `test_netfunnel_commented_out_is_inactive`: `// NetFunnel_Action(...)` 주석만 있으면
     정상 수집된다(현재 프로덕션 상태).
   - `test_card_without_name_skipped`: `<p class="leading-6 ...">`가 비면 그 카드만 빠진다.
   - `test_no_cards_returns_empty`: 카드가 없는 HTML → `[]`.
   - `test_long_name_truncated`(경계): 300자 공고명 → `len(house_nm) <= 200`.
   - `test_dedup_same_pbanc_no`: 같은 `data-pbancNo` 카드가 2번 나오면 1건.

## Deliverables
- `src/collectors/gh.py` (신규)
- `tests/test_gh.py` (신규)

## Verify
- `./.venv/bin/pytest tests/test_gh.py -q` → 전부 통과.
- `./.venv/bin/ruff check src tests` → 클린.
- `grep -rn "www.gh.or.kr" src/` → 결과 없음(robots.txt `Disallow: /` 준수).

## Out of scope
- `pipeline.py` 배선 — Task 07.
- 공고 상세 크롤링(접수기간·세대수·보증금) — 목록에 상세 URL 자체가 없다(D16).
- `www.gh.or.kr`에 대한 모든 요청 — robots.txt 전면 거부.
- NetFunnel 대기열 통과/우회 구현 — D15가 명시적으로 금지한다.
- 경기데이터드림·공공데이터포털 GH 파일데이터 연동(갱신주기 연간이라 실시간 알림에 무용).
