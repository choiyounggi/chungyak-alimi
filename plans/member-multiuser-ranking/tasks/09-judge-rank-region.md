# Task 09: judge_rank 해당지역 1순위 (거주지 ∪ 소득본거지)

## Objective
`src/scoring.py`가 회원의 거주지 ∪ 소득본거지가 공고 지역과 매칭되면 "해당지역"으로
판정해 1순위 자격에 반영한다. 매칭 안 되면 기타지역. 이는 사용자 지정 정책임을 코드에 명시.

## Wiki pages (read these first, only these)
- wiki/testing/quality/minimum-case-set.md — 정상/에러/경계 케이스 선정
- (규칙 자체는 [no-wiki] 도메인. D18/D19 근거)

## Inputs
- Task 08 산출물: `src/regions.py`의 `region_matches(notice_area, member_regions)`.
- 기존 `src/scoring.py`: `judge_rank(notice, house_types, p: Profile, today)` → `{"rank","regulated","reasons"}`. `Profile`(scoring.py). `_CAPITAL`.
- 설계: 순위는 요청 시점 계산(D17). judge_rank는 순수함수 유지.
- 바인딩 결정: D18(지역 매칭), D19(정책 명시).

## Steps
1. `judge_rank`에 회원 지역 정보를 넘길 방법 결정: `Profile`에 순위-지역 필드가 없으므로, `judge_rank`에 선택 인자 `applicant_regions: list[str] | None = None`을 추가(거주지 ∪ 소득본거지 합집합을 호출측이 구성해 전달). 기본 None이면 기존 동작(지역 판정 없음) 유지 — 하위호환.
2. `applicant_regions`가 주어지면: `from .regions import region_matches`; `in_area = region_matches(notice.area_nm, applicant_regions)`.
   - `in_area`가 True면 결과에 `"in_area": True` 및 reasons에 "해당지역(거주지/소득본거지 매칭)" 추가. False면 `"in_area": False` + "기타지역".
   - 정책 주석: 소득본거지 기반 해당지역 인정은 사용자 지정 정책(D19).
3. 기존 1·2순위(통장/예치금/규제) 판정은 그대로 두고, 반환 dict에 `in_area`만 추가(순위 문자열 자체는 기존 규칙 유지 — "해당지역 1순위"는 rank=="1순위" AND in_area로 표현). 대시보드(Task 11)가 `rank`+`in_area`를 조합해 뱃지 표기.
4. 호출측 헬퍼(선택): `judge_notice`가 `applicant_regions`를 받도록 확장하거나, Task 11에서 직접 구성. 최소 변경으로 judge_rank 시그니처만 확장.

## Deliverables
- `src/scoring.py` (judge_rank 확장 — applicant_regions + in_area)
- `tests/test_scoring_region.py` (신규)

## Verify
- `uv run pytest tests/test_scoring_region.py -q 2>&1 | tail -20` 통과(순수함수, DB 불요 — notice/house_types는 경량 스텁 or 기존 pydantic 모델).
- 테스트: ① 거주지 매칭 → `in_area is True` ② 소득본거지만 매칭(거주지 불일치) → `in_area is True` ③ 둘 다 불일치 → `in_area is False` ④ 경계: `applicant_regions=None`이면 기존 반환에 회귀 없음(기존 키 유지) ⑤ 경계: 빈 리스트/빈 area_nm → `in_area is False`.

## Out of scope
- 대시보드 뱃지/필터(Task 11), 지역 정규화 자체(Task 08).
