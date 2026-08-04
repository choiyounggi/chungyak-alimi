# Task 04: HUG 든든전세 collector (물건 목록 → 시도별 공고로 집계)

## Objective
`src/collectors/hug.py`의 `fetch_hug_notices()`가 HUG 든든전세 오픈API의 물건 목록을
**(공고일자 × 시도)** 단위 공고로 집계해 반환한다. `settings.hug_api_key`가 비어 있으면
호출하지 않고 빈 리스트를 반환한다.

## Wiki pages (read these first, only these)
- `wiki/backend/python/boundaries/runtime-validation.md` — 외부 응답을 모델로 파싱한 뒤 필드 접근
- `wiki/backend/common/reliability/timeouts-and-retries.md` — 명시적 타임아웃
- `wiki/security/secrets/secrets-in-code.md` — 신규 API 키를 코드가 아닌 환경변수로 받기
- `wiki/testing/mocking/what-to-mock.md` — 외부 API 페이크

## Inputs
- 엔드포인트: `GET https://www.khug.or.kr/SelectListInfo.do?API_KEY=<키>`
  → 최상위가 **JSON 배열**(봉투 없음). 파라미터는 `API_KEY` 하나뿐(페이징 없음).
- 실측 응답 행(9필드 — 명세의 `RN`은 실제로 없고, `PROP_KIND_CD2_N`이 아니라 `PROP_KIND_CD2_NM`이다):
  ```json
  {"COLL_ANNO_DT":"20260724","SBSR_RCVI_SDT":"20260724100000","SBSR_RCVI_EDT":"20260807170000",
   "AREA_DCD_NM":"서울특별시","AREA_DTL_DCD_NM":"서울 강북구","TMD_NM":"서울특별시 강북구 수유동",
   "PROP_KIND_CD2_NM":"다세대주택","EXUS_ARA":"42.29","LEAS_GUAR_WN":"188100000"}
  ```
- `src/config.py` — `Settings` 클래스(L16~47). `vworld_key` 선언 스타일을 그대로 따른다.
- `.env.example` — 키 블록 주석 스타일.
- `src/collectors/lh.py` — `normalize_region()`(L33~36)을 import 해 재사용(D17).
- Decisions that bind you: D9(모델 파싱), D10(타임아웃 30s·재시도 없음), D12·D29(집계 단위),
  D30(300건 상한 경고), D17(지역 정규화), D23·D24(테스트).

## Steps
1. `src/config.py`의 `Settings`에 필드를 추가한다(`vworld_key` 인근):
   ```python
   # HUG 든든전세 모집공고 오픈API 키(khug.or.kr 발급, data.go.kr 키와 다름)
   hug_api_key: str = ""
   ```
2. `.env.example` 끝에 추가한다:
   ```
   # HUG 든든전세 모집공고 오픈API 키(khug.or.kr 발급)
   HUG_API_KEY=your_hug_api_key
   ```
3. `src/collectors/hug.py`를 만든다.
   ```python
   HUG_URL = "https://www.khug.or.kr/SelectListInfo.do"
   # 목록 API에 공고 상세 링크가 없다 — 든든전세 모집공고 페이지를 고정 링크로 쓴다.
   HUG_NOTICE_URL = "https://www.khug.or.kr/jeonse/web/s07/s070102.jsp"
   ROW_CAP = 300  # 실측 상한(D30)
   ```
4. 물건 1건을 파싱하는 모델 `HugItem(BaseModel)`을 정의한다. `extra="allow"`,
   alias는 원본 대문자 필드명:
   ```python
   coll_anno_dt: str = Field(alias="COLL_ANNO_DT")
   rcvi_sdt: str | None = Field(default=None, alias="SBSR_RCVI_SDT")
   rcvi_edt: str | None = Field(default=None, alias="SBSR_RCVI_EDT")
   area_nm_raw: str = Field(alias="AREA_DCD_NM")
   signgu_nm: str | None = Field(default=None, alias="AREA_DTL_DCD_NM")
   tmd_nm: str | None = Field(default=None, alias="TMD_NM")
   prop_kind: str | None = Field(default=None, alias="PROP_KIND_CD2_NM")
   exus_ara: float | None = Field(default=None, alias="EXUS_ARA")
   leas_guar_wn: int | None = Field(default=None, alias="LEAS_GUAR_WN")
   raw: dict = Field(default_factory=dict, exclude=True)
   ```
   숫자 2필드에 `mode="before"` 검증자: `""`/`None`/숫자 아님 → `None`.
   `_stash_raw` 모델 검증자는 `lh.py:134~139`와 동일하게 둔다.
5. 공고 모델 `HugNotice(BaseModel)`은 통합 스키마 컬럼을 그대로 갖는다
   (`MyhomeNotice`와 같은 필드 집합, `agency: str = "HUG"` 고정). alias 없이 직접 생성한다.
6. 집계 함수(순수 함수 — 네트워크 없음, 테스트하기 쉽다):
   ```python
   def aggregate(items: list[HugItem]) -> list[HugNotice]:
       """물건 목록을 (공고일자 × 시도) 단위 공고로 집계한다(D29)."""
   ```
   그룹 키는 `(coll_anno_dt, area_nm_raw)`. 각 그룹에서:
   - `pblanc_no` = `f"{coll_anno_dt}-{area_nm_raw}"`
   - `house_nm` = `f"HUG 든든전세주택 입주자 모집 ({YYYY-MM-DD}) {정규화지역}"`
     (`coll_anno_dt` 8자리를 `-` 삽입해 표기)
   - `area_nm` = `normalize_region(area_nm_raw)`
   - `house_secd_nm` = 그룹 내 `prop_kind` **최빈값** (동률이면 사전순 첫 값 — 결정적이어야 함)
   - `house_dtl_secd_nm` = `"든든전세"`
   - `bsns_mby_nm` = `"주택도시보증공사"`, `agency` = `"HUG"`
   - `rcrit_pblanc_de` = `coll_anno_dt` → date
   - `rcept_bgnde` / `rcept_endde` = `rcvi_sdt` / `rcvi_edt`의 **앞 8자리** → date
   - `tot_suply_hshldco` = 그룹 행 수
   - `rent_gtn` = 그룹 내 `leas_guar_wn`의 **최솟값**(None 제외, 전부 None이면 None)
   - `hsslpy_adres` = None (읍면동까지만 있어 지오코딩에 부적합 — 넣지 않는다)
   - `pblanc_url` = `HUG_NOTICE_URL`
   - `raw` = `{"_hug_items": [it.raw for it in group], "_area": area_nm_raw}`
   - 반환 순서는 `pblanc_no` 오름차순(결정적)
7. 수집 함수:
   ```python
   def fetch_hug_notices(*, client: httpx.Client | None = None) -> list[HugNotice]:
       """HUG 든든전세 모집공고를 수집한다. 키 미설정이면 빈 리스트."""
   ```
   - `if not settings.hug_api_key: return []`
   - `client or httpx.Client(timeout=30.0)` (D10), `GET HUG_URL, params={"API_KEY": settings.hug_api_key}`
   - `resp.raise_for_status()`; `data = resp.json()`; 리스트가 아니면
     `logger.warning` 후 `[]`
   - `len(data) >= ROW_CAP`이면 D30 경고 로그
   - 행마다 `HugItem.model_validate` 시도, `ValidationError`는 warning 후 스킵(D9)
   - `return aggregate(items)`
   - `finally`에서 `own_client`면 close
8. `tests/test_hug.py`를 만든다. `aggregate()`는 순수 함수라 직접 호출하고,
   `fetch_hug_notices()`는 `httpx.MockTransport`로 검증한다(D23). 케이스(D24):
   - `test_aggregates_by_region`: 서울 2건 + 인천 1건 → 공고 2건,
     서울 공고의 `tot_suply_hshldco == 2`, `area_nm == "서울"`.
   - `test_rent_gtn_is_group_minimum`: 보증금 1.8억/2.6억 → `rent_gtn == 180_000_000`.
   - `test_house_secd_nm_is_mode_and_deterministic`: 다세대 2 + 오피스텔 2(동률) →
     사전순 첫 값으로 고정, 두 번 호출해도 같다.
   - `test_receipt_dates_parsed_from_14_digits`: `"20260807170000"` → `date(2026, 8, 7)`.
   - `test_bad_row_skipped`: `COLL_ANNO_DT` 없는 행이 섞여도 나머지가 집계된다.
   - `test_empty_response_returns_empty`: `[]` → `[]` (예외 없음).
   - `test_no_api_key_returns_empty_without_request`: 키가 `""`면 transport가
     한 번도 호출되지 않는다.
   - `test_row_cap_warning`(경계): 300행이면 경고 로그가 남는다(`caplog`).

## Deliverables
- `src/collectors/hug.py` (신규)
- `tests/test_hug.py` (신규)
- `src/config.py` (수정 — 1줄 추가)
- `.env.example` (수정 — 2줄 추가)

## Verify
- `./.venv/bin/pytest tests/test_hug.py -q` → 전부 통과.
- `./.venv/bin/ruff check src tests` → 클린.
- `git diff --stat src/config.py` → 1줄 추가만(다른 설정 변경 없음).

## Out of scope
- `pipeline.py` 배선 — Task 07.
- 물건 단위 개별 Notice 생성 — 식별자·번지가 없어 PK를 만들 수 없다(D12 실측 근거).
- HUG 공고문 PDF 다운로드·상세 크롤링.
