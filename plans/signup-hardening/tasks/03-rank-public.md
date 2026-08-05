# Task 03: 국민주택 1·2순위 + 거주기간/예비신혼 검증

## Objective
청약통장 **납입 횟수**가 실제로 순위에 반영되도록 국민주택(공공) 순위 판정을 추가하고,
`judge_notice` 가 공고 유형에 따라 민영/국민을 분기한다. 지역별 거주기간과
예비신혼 파트너의 자가 보유 여부를 판정 사유에 반영한다.

## Wiki pages (read these first, only these)
- backend/python/boundaries/runtime-validation.md — 순수함수 경계, Optional 처리
- testing/quality/minimum-case-set.md — 규칙 경계값(가입기간/납입횟수 경계) 테스트

## Inputs
- **Task 01 산출물(계약)**: `scoring.Profile` 의 신규 필드
  `owns_car: bool`, `account_payment_count: int`, `residence_history: list[ResidencePeriod]`,
  `preferred_types: list[str]`, `partners: list[PartnerInfo]` 및 `ResidencePeriod(region, since)`,
  `PartnerInfo(label, lives_with_parents, owns_home, residence_region, income_base_region)`.
- 기존 `src/scoring.py` `judge_rank`(민영), `_is_regulated`, `_full_years`, `_CAPITAL`,
  `judge_newlywed`, `judge_first_life`, `judge_notice`, 규칙 상수 블록(최상단).
- 기존 `src/regions.py` `region_matches` / `normalize_region`.
- 바인딩 결정: R1(국민 순위 기준), R2(유형 분기), R3(거주기간은 사유만), R4(신혼 7년),
  R5(예비신혼 자가), R6(차량가액은 공공 자산요건에만).

## Steps
1. 최상단 규칙 상수 블록에 국민주택 상수를 추가한다(주석에 근거·확인시점 명시):
   `PUBLIC_ACCOUNT_MONTHS = {"regulated": 24, "capital": 12, "other": 6}`,
   `PUBLIC_PAYMENT_COUNTS = {"regulated": 24, "capital": 12, "other": 6}`,
   공공 특별공급 자산요건 상한(부동산·자동차) 상수.
2. `judge_rank_public(notice, p, today=None, applicant_regions=None) -> dict` 추가:
   - 규제지역 여부는 기존 `_is_regulated(notice.raw)` 재사용(중복 정의 금지).
   - 지역군 판정: 규제 → `regulated`, `notice.area_nm ∈ _CAPITAL` → `capital`, 그 외 `other`.
   - 가입기간(`p.account.opened` 기준 개월) 미달 / `p.account_payment_count` 미달 /
     `not p.household_all_homeless` / (규제지역인데 `not p.is_household_head`) → 각각 사유.
   - 사유가 하나라도 있으면 `"2순위"`, 없으면 `"1순위"`.
   - 반환 dict 는 `judge_rank` 와 **같은 키**(`rank`/`regulated`/`reasons`/`in_area`),
     `applicant_regions` 처리도 `judge_rank` 와 동일 규칙(D19: 지역은 순위에 영향 없음).
   - `p.account.opened is None` 이면 가입기간 0으로 계산(예외 금지).
3. `residence_years_in(p, region, today=None) -> float` 추가:
   `p.residence_history` 에서 `normalize_region(h.region) == normalize_region(region)` 인
   항목의 `since` 로부터 경과 연수 중 **최대값**. 없거나 `since=None` 이면 `0.0`.
4. 규제지역 공고면 해당지역 우선공급 거주기간(기본 2년, 상수 `REGULATED_RESIDENCE_YEARS`)
   미달 시 `judge_rank`·`judge_rank_public` 의 `reasons` 에 표기한다.
   **순위는 낮추지 않는다**(R3 — 기존 `blocking` 계산에 포함시키지 않을 것).
5. `judge_newlywed` 에 예비신혼 검증 추가(R5): `p.partners` 중 `owns_home` 이 True인 사람이
   있으면 부적격 사유 추가. 정책이 공식 규칙과 다를 수 있음을 docstring에 명시.
6. `judge_notice` 를 분기(R2): 공고의 `house_dtl_secd_nm` 이 "민영"이면 기존 경로,
   "국민"이거나 source 가 `lh`/`myhome`/`sh`/`gh` 계열이면 `judge_rank_public` 경로로
   `supported=True` 를 돌려주고 `rank` 키에 그 결과를 담는다. 어느 쪽도 아니면 기존대로
   `supported=False`. 반환 dict 에 `housing_type: "민영"|"국민"` 을 추가한다.
   기존 민영 응답의 키(`score`/`rank`/`newlywed`/`first_life`/`summary`)는 **그대로 유지**한다
   — `src/web/app.py` 와 `tests/test_scoring.py` 가 이미 소비 중이다.

## Deliverables
- `src/scoring.py` (상수 + judge_rank_public + residence_years_in + judge_newlywed 보강 + judge_notice 분기)
- `tests/test_scoring_public.py` (신규)

## Verify
- `uv run pytest tests/test_scoring_public.py tests/test_scoring.py tests/test_scoring_region.py tests/test_my_rank.py -q 2>&1 | tail -20` 통과
  (**기존 테스트가 하나도 깨지지 않아야 한다** — 민영 경로 회귀 금지).
- `uv run ruff check src tests 2>&1 | tail -5` clean.
- 테스트(DB 불필요, 가짜 notice 객체): ① 정상 — 수도권 국민주택, 가입 13개월·납입 13회·
  무주택 → 1순위 ② 에러 — 납입 11회(가입기간은 충족) → 2순위 + 사유에 "납입횟수" 포함 /
  유주택 세대 → 2순위 / 규제지역인데 세대주 아님 → 2순위
  ③ 경계 — 납입횟수 정확히 12회(수도권, 1순위) vs 11회(2순위), `account.opened=None`(2순위,
  예외 없음), `residence_history=[]` 일 때 `residence_years_in` 이 0.0, 규제지역 거주 1년
  → 사유는 붙되 **rank 는 1순위 유지**(R3), `partners=[]` 일 때 기존 신혼 판정 불변.

## Out of scope
- 대시보드/템플릿 반영(Task 06), 프로필 입력 폼(Task 05), db.py/members.py(Task 01).
