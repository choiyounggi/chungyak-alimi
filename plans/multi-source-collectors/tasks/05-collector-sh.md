# Task 05: SH collector (서울주거포털 임대·분양 목록 HTML 파싱)

## Objective
`src/collectors/sh.py`의 `fetch_sh_notices()`가 서울주거포털의 SH 임대·분양 공고 목록
두 페이지를 파싱해 통합 스키마 모델 리스트를 반환한다. 신규 의존성 없이 표준
라이브러리만 쓴다.

## Wiki pages (read these first, only these)
- `wiki/security/input/validation-at-trust-boundaries.md` — 외부 HTML에서 뽑은 문자열의 검증·길이 상한·URL 스킴 허용목록
- `wiki/backend/common/reliability/timeouts-and-retries.md` — 명시적 타임아웃
- `wiki/testing/quality/minimum-case-set.md` — 케이스 선정
- `wiki/testing/data/test-data-and-isolation.md` — 고정 HTML 픽스처로 격리

## Inputs
- 대상 URL 2개 (robots.txt `Allow: /` 확인됨 — 2026-08-04):
  - 임대 `https://housing.seoul.go.kr/site/main/sh/publicLease/list`
  - 분양 `https://housing.seoul.go.kr/site/main/sh/publicSale/01/list`
- **실측 행 마크업**(임대, 공백/주석 그대로):
  ```html
  <tr>
    <td class="td1">77</td>
    <td class="td3">청년안심주택</td> <!-- 2021-01-25 클래스 수정 -->
    <td class="txl td-m">
      <!--	<a href="/site/main/sh/publicLease/view?seq=1&cp=1&amp;supplyType=publicLease"></a>-->
      2026년 2차 청년안심주택(공공임대) 입주자 모집공고
    </td>
    <td class="td4">  2026-07-31  </td>
    <td class="td-mdisn">  2026-12-11  </td>
    <td class="td-mdisn">모집중</td>
    <td class="td-mdisn">맞춤주택공급부</td>
    <td class="td5"><a href="https://www.i-sh.co.kr/main/lay2/program/S1T294C295/www/brd/m_241/view.do?seq=307835" class="btn-gray" title="새창 이동" target="_blank">바로가기</a></td>
  </tr>
  ```
- 컬럼 구성이 두 게시판에서 다르다:
  - 임대 8칸 `[번호, 청약유형, 공고명, 공고게시일, 발표일, 모집상태, 담당부서, 링크]`
  - 분양 7칸 `[번호, 청약유형, 공고명, 공고게시일, 발표일, 담당부서, 링크]` (모집상태 없음)
- `src/collectors/lh.py` — `_safe_url` 검증자(L89~94) 패턴, `ValidationError` 스킵 루프(L352~360).
- Decisions that bind you: D9(모델 파싱 후 접근), D10(타임아웃 30s), D13(표준 라이브러리만),
  D14(신뢰 경계·길이 상한·URL 허용목록), D16(접수기간 없음 → None, `모집중`만 수집),
  D23·D24(테스트).

## Steps
1. `src/collectors/sh.py`를 만든다. 상수:
   ```python
   SH_BASE = "https://housing.seoul.go.kr"
   BOARDS = (
       ("lease", "/site/main/sh/publicLease/list"),
       ("sale", "/site/main/sh/publicSale/01/list"),
   )
   # D14 — 목록에서 뽑은 링크는 이 두 호스트만 허용한다(리다이렉트·오염 방지).
   ALLOWED_LINK_HOSTS = ("housing.seoul.go.kr", "www.i-sh.co.kr")
   MAX_NAME_LEN = 200
   MAX_URL_LEN = 500
   ```
2. HTML 헬퍼 3개를 순수 함수로 만든다(테스트 대상):
   ```python
   _COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
   _TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
   _TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
   _TAG_RE = re.compile(r"<[^>]+>")
   _HREF_RE = re.compile(r'href="([^"]+)"', re.I)


   def _strip_comments(html: str) -> str:
       """주석을 먼저 제거한다 — 공고명 칸에 주석 처리된 <a href>가 들어있어
       그대로 두면 링크 추출이 그 값을 잘못 집는다(실측)."""
       return _COMMENT_RE.sub("", html)


   def _text(cell: str) -> str:
       """셀 HTML → 공백 정규화된 순수 텍스트."""
       return " ".join(html.unescape(_TAG_RE.sub(" ", cell)).split())
   ```
   `_rows(page_html)`: `_strip_comments` → `_TR_RE`로 행, 각 행에서 `_TD_RE`로 셀 목록을
   반환. 셀이 7개 미만인 행(헤더 등)은 건너뛴다.
3. 행 → dict 매핑 `_parse_row(tds: list[str], board: str) -> dict | None`:
   - `texts = [_text(td) for td in tds]`
   - 칸 수로 분기: `len(texts) >= 8`이면 `status = texts[5]`, 담당부서 `texts[6]`;
     `len(texts) == 7`이면 `status = None`, 담당부서 `texts[5]`.
   - 링크: **마지막 셀**(`tds[-1]`)에서만 `_HREF_RE`로 뽑는다. 상대경로면 `urljoin(SH_BASE, href)`.
     `https://`가 아니거나 host가 `ALLOWED_LINK_HOSTS`에 없으면 `None`(D14).
   - `native`: 링크 쿼리스트링의 `seq` 값(`parse_qs`) → `f"{board}-{seq}"`.
     `seq`가 없으면 `f"{board}-{게시일}-{hashlib.sha1(공고명.encode()).hexdigest()[:8]}"`.
     (번호 칸 `texts[0]`은 게시물 증가에 따라 밀릴 수 있어 PK로 쓰지 않는다.)
   - 공고명이 비었으면 `None` 반환(그 행 스킵).
4. `ShNotice(BaseModel)`을 정의한다. 통합 스키마 필드 집합은 `MyhomeNotice`와 동일하고
   `agency: str = "SH"` 고정. 매핑:
   - `pblanc_no` = 위 `native`
   - `house_nm` = 공고명. **`[정정]` 접두는 `[정정공고]`로 치환**해 기존 정정 판정 규약에
     맞춘다(실측: `"[정정] 2026년 재개발임대주택 일반모집 공고(2026. 7. 30.)"`).
     치환 후 `MAX_NAME_LEN`으로 자른다(D14).
   - `house_manage_no` = `None` → 이름 기반 정정 그룹핑 경로를 탄다(Task 02 D25의 else 가지).
   - `house_secd_nm` = 청약유형(`texts[1]`), `house_dtl_secd_nm` = 청약유형과 동일 값
   - `area_nm` = `"서울"` 고정 (서울주거포털은 서울 전용)
   - `bsns_mby_nm` = `"서울주택도시공사"`, `agency` = `"SH"`
   - `rcrit_pblanc_de` = 공고게시일(`YYYY-MM-DD`), `przwner_presnatn_de` = 발표일
     (`-`이거나 비면 `None`)
   - `rcept_bgnde` / `rcept_endde` = `None` (D16)
   - `rent_gtn` / `mt_rntchrg` / `tot_suply_hshldco` = `None`
   - `pblanc_url` = 검증된 링크(`MAX_URL_LEN` 초과 시 `None`)
   - `raw` = `{"_sh_board": board, "_sh_status": status, "_sh_dept": 담당부서, "_sh_row_no": texts[0]}`
5. 수집 함수:
   ```python
   def fetch_sh_notices(*, client: httpx.Client | None = None) -> list[ShNotice]:
       """서울주거포털에서 SH 임대·분양 공고 목록을 수집한다."""
   ```
   - `client or httpx.Client(timeout=30.0, follow_redirects=True)` (D10)
   - 게시판 2개를 순회. `resp.raise_for_status()` 후 `resp.text`.
   - **임대 게시판은 `status == "모집중"`인 행만** 채택(D16). 분양은 status가 없으므로 전량.
   - `ShNotice.model_validate` 실패는 warning 후 그 행만 스킵(D9).
   - `pblanc_no` 기준 중복 제거.
6. `tests/test_sh.py`를 만든다. 위 **실측 마크업을 그대로 담은 문자열 픽스처**를 쓰고
   `httpx.MockTransport`로 두 URL에 각각 응답을 준다(D23). 케이스(D24):
   - `test_parses_real_row`: `pblanc_no == "lease-307835"`, `area_nm == "서울"`,
     `agency == "SH"`, `house_secd_nm == "청년안심주택"`,
     `rcrit_pblanc_de == date(2026, 7, 31)`, `przwner_presnatn_de == date(2026, 12, 11)`,
     `pblanc_url`이 `i-sh.co.kr`로 시작.
   - `test_ignores_commented_out_anchor`: 공고명 칸의 주석 `<a href="...view?seq=1">`이
     링크로 잘못 잡히지 않는다 — `pblanc_no`가 `"lease-1"`이 **아니다**.
   - `test_skips_non_recruiting_rows`: `모집중`이 아닌 임대 행은 제외된다.
   - `test_sale_board_seven_columns`: 7칸 행(모집상태 없음)도 파싱되고 담당부서가
     `texts[5]`에서 나온다.
   - `test_correction_prefix_normalized`: `"[정정] ○○"` → `house_nm`이
     `"[정정공고]"`로 시작.
   - `test_rejects_foreign_host_link`: `href="https://evil.example.com/x"` →
     `pblanc_url is None` (D14).
   - `test_long_name_truncated`(경계): 300자 공고명 → `len(house_nm) <= 200`.
   - `test_empty_table_returns_empty`: `<table></table>`만 있으면 `[]`.

## Deliverables
- `src/collectors/sh.py` (신규)
- `tests/test_sh.py` (신규)

## Verify
- `./.venv/bin/pytest tests/test_sh.py -q` → 전부 통과.
- `./.venv/bin/ruff check src tests` → 클린.
- `grep -rn "i-sh.co.kr" src/collectors/sh.py` → **링크 허용목록에만** 등장하고,
  요청을 보내는 코드에는 없어야 한다(robots.txt `Disallow: /` 준수).

## Out of scope
- `pipeline.py` 배선 — Task 07.
- 공고 상세 페이지 크롤링(접수기간·세대수 확보) — D16으로 제외.
- `www.i-sh.co.kr`에 대한 모든 HTTP 요청 — robots.txt가 전면 거부한다. 링크로만 쓴다.
- 목록 2페이지 이상 페이징 — 1페이지(최신 15건)만 본다. 하루 2회 배치로 충분히 따라잡는다.
