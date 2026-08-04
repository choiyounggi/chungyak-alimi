# Task 02: 임대 공고가 통과하도록 필터 정책 재설계 + 기관 필터축 추가

## Objective
`FilterConfig`에 `agencies`·`rent_deposit_max_manwon` 두 축이 생기고, `영구임대`·`국민임대`
공고가 더 이상 제외키워드로 탈락하지 않는다. `find_superseded()`가 주택관리번호를 가진
모든 소스에서 정정공고 대체를 판정한다.

## Wiki pages (read these first, only these)
- `wiki/testing/quality/minimum-case-set.md` — 필터 분기별 케이스·경계값 선정
- `wiki/testing/quality/tests-that-cannot-fail.md` — 통과만 하는 필터 테스트를 만들지 않기

## Inputs
- `src/filters.py` — `FilterConfig`(L19~27), `load_filter_config()`(L30~32),
  `find_superseded()`의 그룹핑 분기(L71~79), `match_notice()`(L100~148).
- `config/filters.yaml` — 현재 `exclude_keywords: ["고령자","실버","영구임대","국민임대"]`,
  `price_max_manwon: 80000`.
- Task 01 산출물: `notice.rent_gtn`(원 단위 BigInteger), `notice.agency`(LH/SH/GH/HUG/기타),
  `src/db.py`의 `AGENCIES` 상수.
- `tests/test_filters.py` — 기존 테스트 패턴.
- Decisions that bind you: D18(제외키워드에서 영구임대·국민임대 제거, 임대 상한은 별도 축),
  D19(`agencies: []` = 전체), D25(주택관리번호 그룹 일반화), D24(케이스 최소 집합).

## Steps
1. `src/filters.py`의 `FilterConfig`에 필드 2개를 추가한다(기존 필드 뒤, `only_open` 앞):
   ```python
   agencies: list[str] = []                       # 공급기관 LH/SH/GH/HUG/기타. [] = 전체
   rent_deposit_max_manwon: int | None = None     # 임대보증금 상한(만원). None = 제한없음
   ```
2. `match_notice()`에 판정 2개를 추가한다. 위치는 기존 `exclude_keywords` 판정 **바로 앞**:
   ```python
   agency = getattr(notice, "agency", None)
   if cfg.agencies and agency not in cfg.agencies:
       fails.append(f"기관:{agency}")

   # 임대보증금 상한(원 단위 컬럼 vs 만원 단위 설정). 보증금 정보가 없으면(분양 등) 보류.
   if cfg.rent_deposit_max_manwon is not None:
       deposit = getattr(notice, "rent_gtn", None)
       if deposit is not None and deposit > cfg.rent_deposit_max_manwon * 10000:
           fails.append("임대보증금초과")
   ```
3. `find_superseded()`의 그룹핑 분기(L71~79)에서 조건을 소스명이 아니라 값 유무로 바꾼다.
   **이 한 줄만** 바꾸고 나머지 로직·주석은 그대로 둔다:
   ```python
   # 변경 전: if source == "applyhome":
   # 변경 후:
   if n.house_manage_no:
       key = f"hmn:{n.house_manage_no}"
   ```
   기존 키 접두사 `applyhome:hmn:`를 `hmn:`로 바꾸는 이유: 마이홈 정정공고는
   `house_manage_no`에 원공고 id를 실어 같은 그룹에 들어와야 한다(D25).
   `else` 가지(이름 기반)는 그대로다. LH는 `house_manage_no`가 항상 None이라 영향 없다.
4. 같은 함수의 docstring 첫 불릿을 사실에 맞게 고친다:
   `- 청약홈: 같은 주택관리번호(house_manage_no) 그룹에서 최신 공고만 남긴다.`
   → `- 주택관리번호(house_manage_no)가 있는 소스(청약홈·마이홈): 같은 번호 그룹에서 최신만 남긴다.`
5. `config/filters.yaml`을 아래로 고친다(주석 포함):
   ```yaml
   regions: ["서울", "경기", "인천"]   # 관심 지역 (area_nm). [] = 전국
   agencies: []                       # 공급기관: [] = 전체(LH/SH/GH/HUG/기타)
   house_types: []                    # 주택유형: [] = 전체(모두)
   supply_types: []                   # 공급유형: [] = 전체(모두)
   special_supply: ["생애최초", "신혼부부"]  # 관심 특별공급
   min_households: null               # 총공급세대 하한: null = 제한없음
   price_max_manwon: 80000            # 분양가 상한(만원): 8억 — 분양가 정보가 있는 공고에만 적용
   rent_deposit_max_manwon: null      # 임대보증금 상한(만원): null = 제한없음
   exclude_keywords: ["고령자", "실버"]  # 제외 키워드 — 연령제한(고령자·실버)만. 영구임대·국민임대는 2026-08-04 제거(임대 수집 확장)
   only_open: true                    # 접수마감 지난 공고 제외(미래/진행 청약만)
   ```
6. `tests/test_filters.py`에 테스트를 추가한다. 기존 테스트가 쓰는 notice 더미 만드는
   방식을 그대로 따르고, 새 속성은 더미에 `agency`/`rent_gtn`을 달아 표현한다:
   - `test_agency_filter_passes_when_empty`: `agencies=[]`면 어떤 agency든 통과.
   - `test_agency_filter_rejects_other_agency`: `agencies=["LH"]` + `agency="SH"` →
     `matched is False`이고 사유에 `"기관:SH"` 포함.
   - `test_rent_deposit_over_limit_fails`: `rent_deposit_max_manwon=15000`(1.5억) +
     `rent_gtn=200_000_000` → 사유에 `"임대보증금초과"`.
   - `test_rent_deposit_at_limit_passes`(경계): `rent_gtn=150_000_000` → 통과.
   - `test_rent_deposit_none_is_skipped`: `rent_gtn=None`(분양 공고) → 보증금 사유 없음.
   - `test_public_rental_keyword_no_longer_excluded`: `house_nm="○○ 국민임대주택 예비입주자 모집"`
     + 기본 `config/filters.yaml` 로드 → 제외키워드로 탈락하지 **않는다**.
   - `test_superseded_groups_by_house_manage_no_for_any_source`: `source="myhome"`인
     공고 2건이 같은 `house_manage_no`를 갖고 하나가 `[정정공고]` 접두일 때, 원공고가
     대체 대상으로 잡힌다.

## Deliverables
- `src/filters.py` (수정)
- `config/filters.yaml` (수정)
- `tests/test_filters.py` (수정 — 테스트 7개 추가)

## Verify
- `./.venv/bin/pytest tests/test_filters.py -q` → 전부 통과.
- `./.venv/bin/pytest -q` → 기존 테스트 회귀 없음(특히 `tests/test_supersede.py`).
- `./.venv/bin/ruff check src tests` → 클린.

## Out of scope
- collector 작성(03~06) — 이 태스크는 판정 로직만 다룬다.
- `scoring.py`의 순위 판정 분기(D20) — Task 07이 처리한다.
- 웹 대시보드의 기관 칩(08).
