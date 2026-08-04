# Task 03: 마이홈포털 collector (공공임대 + 공공분양)

## Objective
`src/collectors/myhome.py`의 `fetch_myhome_notices()`가 마이홈 오픈API 두 오퍼레이션을
페이징 순회해 통합 `Notice` 스키마에 맞춘 pydantic 모델 리스트를 반환한다. 네트워크 없이
`httpx.MockTransport`로 검증된다.

## Wiki pages (read these first, only these)
- `wiki/backend/python/boundaries/runtime-validation.md` — 외부 API 응답을 모델로 파싱한 뒤에만 필드 접근
- `wiki/backend/common/reliability/timeouts-and-retries.md` — 아웃바운드 호출의 명시적 타임아웃
- `wiki/testing/mocking/what-to-mock.md` — 소유하지 않은 외부 API를 페이크로 대체
- `wiki/testing/quality/minimum-case-set.md` — 케이스 선정

## Inputs
- 기존 패턴 원본: `src/collectors/lh.py` — `LhNotice` 모델의 "통합 notice 컬럼 호환용"
  블록(L53~65), `_stash_raw` 모델 검증자(L67~75), 날짜 검증자(L77~87),
  `_safe_url` 검증자(L89~94), `ValidationError` 행 스킵 루프(L352~360),
  그리고 `normalize_region()`(L33~36) — **이 함수를 import 해서 재사용한다**(D17).
- `src/config.py` — `settings.odcloud_api_key` (마이홈도 같은 data.go.kr 인증키를 쓴다).
- 실측 응답 필드(임대 `/rsdtRcritNtcList`, 32개):
  `pblancId, houseSn, sttusNm, pblancNm, suplyInsttNm, houseTyNm, suplyTyNm,`
  `beforePblancId, rcritPblancDe, przwnerPresnatnDe, suplyHoCo, refrnc, url, pcUrl,`
  `mobileUrl, hsmpNm, brtcNm, signguNm, fullAdres, rnCodeNm, refrnLegaldongNm, pnu,`
  `heatMthdNm, totHshldCo, sumSuplyCo, rentGtn, enty, prtpay, surlus, mtRntchrg,`
  `beginDe, endDe`
- 실측 응답 필드(분양 `/ltRsdtRcritNtcList`, 27개): 위에서 `suplyTyNm, suplyHoCo,`
  `totHshldCo, rentGtn, mtRntchrg`가 **없다**.
- 응답 봉투: `{"response": {"header": {"resultCode": "00", ...}, "body": {"totalCount": "372", "numOfRows": "100", "pageNo": "1", "item": [...]}}}`
- Decisions that bind you: D9(모델 파싱 후 필드 접근·행 단위 스킵), D10(타임아웃 30s·재시도 없음),
  D11(numOfRows=100·최대 20페이지), D17(`normalize_region` 재사용), D23·D24(테스트),
  D26(정정공고 접두사), D27(1행=1Notice·houseSn 포함 키), D28(pnu는 raw 보관만).

## Steps
1. `src/collectors/myhome.py`를 만든다. 상수:
   ```python
   MYHOME_BASE = "https://apis.data.go.kr/1613000/HWSPR02"
   RENT_PATH = "/rsdtRcritNtcList"      # 공공임대 모집공고
   SALE_PATH = "/ltRsdtRcritNtcList"    # 공공분양 모집공고
   ```
2. `MyhomeNotice(BaseModel)`를 정의한다. `model_config = ConfigDict(populate_by_name=True, extra="allow")`.
   `LhNotice`와 같은 "통합 notice 컬럼 호환용" 구조를 따르되, 원본 필드명을 alias로 쓰지 말고
   **`model_validator(mode="before")`에서 통합 스키마로 매핑**한다(임대·분양 응답 모양이 달라서
   alias 하나로 두 형태를 못 받는다):
   ```python
   class MyhomeNotice(BaseModel):
       model_config = ConfigDict(populate_by_name=True, extra="allow")

       pblanc_no: str            # f"{pblancId}-{houseSn}" (D27)
       house_manage_no: str      # f"{beforePblancId or pblancId}-{houseSn}" (D25·D27)
       house_nm: str
       house_secd_nm: str | None = None      # houseTyNm
       house_dtl_secd_nm: str | None = None  # suplyTyNm, 분양은 "공공분양"
       rent_secd_nm: str | None = None
       area_nm: str | None = None            # normalize_region(brtcNm)
       hsslpy_adres: str | None = None       # fullAdres
       bsns_mby_nm: str | None = None        # suplyInsttNm
       agency: str = "기타"                   # suplyInsttNm=="LH" → "LH" (D8)
       rcrit_pblanc_de: date | None = None
       rcept_bgnde: date | None = None       # beginDe
       rcept_endde: date | None = None       # endDe
       spsply_rcept_bgnde: date | None = None
       spsply_rcept_endde: date | None = None
       przwner_presnatn_de: date | None = None
       tot_suply_hshldco: int | None = None  # sumSuplyCo
       mvn_prearnge_ym: str | None = None
       pblanc_url: str | None = None         # url
       rent_gtn: int | None = None           # rentGtn(원)
       mt_rntchrg: int | None = None         # mtRntchrg(원)
       raw: dict = Field(default_factory=dict, exclude=True)
   ```
3. `@model_validator(mode="before")` 클래스메서드 `_map`을 쓴다. 입력 dict는 API 원본 행에
   `"_kind"` 키(`"rent"` 또는 `"sale"`)가 더해진 형태로 들어온다:
   ```python
   @model_validator(mode="before")
   @classmethod
   def _map(cls, d):
       if not isinstance(d, dict) or "pblanc_no" in d:
           return d
       pid, sn = str(d.get("pblancId") or ""), str(d.get("houseSn") or "")
       base = str(d.get("beforePblancId") or "") or pid
       nm = str(d.get("pblancNm") or "").strip()
       if d.get("sttusNm") == "정정공고":
           nm = f"[정정공고]{nm}"           # D26 — 기존 접두사 규약과 통일
       inst = d.get("suplyInsttNm") or None
       return {
           "pblanc_no": f"{pid}-{sn}",
           "house_manage_no": f"{base}-{sn}",
           "house_nm": nm,
           "house_secd_nm": d.get("houseTyNm") or None,
           "house_dtl_secd_nm": d.get("suplyTyNm") or ("공공분양" if d.get("_kind") == "sale" else None),
           "area_nm": normalize_region(d.get("brtcNm")),
           "hsslpy_adres": (d.get("fullAdres") or "").strip() or None,
           "bsns_mby_nm": inst,
           "agency": "LH" if inst == "LH" else "기타",
           "rcrit_pblanc_de": d.get("rcritPblancDe"),
           "rcept_bgnde": d.get("beginDe"),
           "rcept_endde": d.get("endDe"),
           "przwner_presnatn_de": d.get("przwnerPresnatnDe"),
           "tot_suply_hshldco": d.get("sumSuplyCo"),
           "pblanc_url": d.get("url"),
           "rent_gtn": d.get("rentGtn"),
           "mt_rntchrg": d.get("mtRntchrg"),
           "raw": {k: v for k, v in d.items() if k != "_kind"},
       }
   ```
   `house_nm`이 빈 문자열이면 `ValidationError`가 나야 하므로 `house_nm: str`에
   `Field(min_length=1)`을 건다.
4. 검증자 3개를 단다:
   - 날짜 4필드(`rcrit_pblanc_de`,`rcept_bgnde`,`rcept_endde`,`przwner_presnatn_de`)에
     `mode="before"`: 값이 8자리 숫자면 `f"{s[0:4]}-{s[4:6]}-{s[6:8]}"`, 아니면 `None`.
   - 숫자 3필드(`tot_suply_hshldco`,`rent_gtn`,`mt_rntchrg`)에 `mode="before"`:
     `""`/`None`/숫자 아님 → `None`.
   - `pblanc_url`에 `mode="before"`: `http://`/`https://`로 시작하지 않으면 `None`
     (`lh.py:89~94`와 동일).
5. 응답 파서 `_items(body)`: `body["response"]["body"]["item"]`을 안전하게 꺼낸다.
   `item`이 없거나 dict 단건이면 리스트로 정규화하고, `resultCode`가 `"00"`이 아니면
   `logger.warning` 후 `[]`를 반환한다(LH의 `_ss_ok`와 같은 역할).
   `totalCount`는 `int(...)`로 파싱하되 실패 시 `0`.
6. 수집 함수:
   ```python
   def fetch_myhome_notices(
       *, per_page: int = 100, max_pages: int = 20, client: httpx.Client | None = None
   ) -> list[MyhomeNotice]:
       """마이홈포털 공공임대·공공분양 모집공고를 수집한다."""
   ```
   - `own_client = client is None`; `client = client or httpx.Client(timeout=30.0)` (D10).
   - `for path, kind in ((RENT_PATH, "rent"), (SALE_PATH, "sale")):` 각각
     `pageNo`를 1부터 `max_pages`까지 올리며 GET.
     params: `{"serviceKey": settings.odcloud_api_key, "numOfRows": per_page, "pageNo": page, "type": "json"}`.
   - `resp.raise_for_status()` 후 `resp.json()`.
   - 각 행에 `{**row, "_kind": kind}`로 `MyhomeNotice.model_validate` 시도,
     `ValidationError`는 `logger.warning("마이홈 공고 파싱 실패 스킵(%s): %s", kind, e)` 후 continue (D9).
   - `pblanc_no` 기준 `seen` 집합으로 오퍼레이션 간 중복 제거.
   - 종료 조건: 이번 페이지 `item`이 비었거나, 누적 수집 행 수가 `totalCount` 이상이거나,
     `len(items) < per_page` (D11).
   - `finally`에서 `own_client`면 `client.close()`.
7. `tests/test_myhome.py`를 만든다. `httpx.MockTransport`로 두 경로를 구분해 고정 응답을
   돌려주고, `httpx.Client(transport=...)`를 주입한다(D23 — 실제 네트워크 금지).
   샘플 행은 아래 실측값을 축약해 쓴다:
   ```python
   RENT_ROW = {
       "pblancId": "20942", "houseSn": 1, "sttusNm": "정정공고",
       "pblancNm": "물금2 천년나무 행복주택 잔여물량 모집", "suplyInsttNm": "LH",
       "houseTyNm": "아파트", "suplyTyNm": "행복주택", "beforePblancId": "20893",
       "rcritPblancDe": "20260701", "przwnerPresnatnDe": "20260813",
       "brtcNm": "경상남도", "signguNm": "양산시",
       "fullAdres": "경상남도 양산시 물금읍 청운로 42 ",
       "pnu": "4833025321108960001", "sumSuplyCo": 70,
       "rentGtn": 10800000, "mtRntchrg": 54540,
       "beginDe": "20260713", "endDe": "20260812",
       "url": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?panId=2015122300020501",
   }
   ```
   케이스(D24):
   - `test_maps_rent_row`: `pblanc_no == "20942-1"`, `house_manage_no == "20893-1"`,
     `house_nm`이 `"[정정공고]"`로 시작, `area_nm == "경남"`, `agency == "LH"`,
     `rent_gtn == 10800000`, `rcept_endde == date(2026, 8, 12)`.
   - `test_sale_row_gets_default_supply_type`: 분양 행(`suplyTyNm` 없음) →
     `house_dtl_secd_nm == "공공분양"`, `rent_gtn is None`.
   - `test_non_lh_agency_is_etc`: `suplyInsttNm="인천도시공사"` → `agency == "기타"`.
   - `test_bad_row_is_skipped`: `pblancNm`이 `""`인 행이 섞이면 그 행만 빠지고 나머지는 남는다.
   - `test_empty_item_stops_paging`: 1페이지가 `{"item": []}`면 빈 리스트를 반환하고
     추가 요청이 없다(MockTransport 호출 횟수로 확인).
   - `test_non_ok_result_code_returns_empty`: `resultCode="30"` → `[]`.
   - `test_dedup_across_operations`: 같은 `pblancId`+`houseSn`이 임대·분양 양쪽에 오면 1건.

## Deliverables
- `src/collectors/myhome.py` (신규)
- `tests/test_myhome.py` (신규)

## Verify
- `./.venv/bin/pytest tests/test_myhome.py -q` → 전부 통과.
- `./.venv/bin/ruff check src tests` → 클린.
- 테스트 실행 중 실제 외부 요청이 없어야 한다(모든 `httpx.Client`가 주입된 transport 사용).

## Out of scope
- `pipeline.py` 배선 — Task 07.
- `raw["pnu"]`를 이용한 폴리곤 보강(D28) — 후속 작업.
- 마이홈 단지정보(`HWSPR04`)·예비입주자(`HWSPR03`) API.
- 주택형(`notice_house_type`) 적재 — 마이홈 목록에 주택형별 분해가 없다.
