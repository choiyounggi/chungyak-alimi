# Task 08: 지역 정규화 유틸 (area_nm 표본조사 + 예외맵)

## Objective
`src/regions.py`가 공고 지역명(`notice.area_nm`)과 회원 지역 문자열을 같은 정규형으로
바꾸는 `normalize_region(s)`와, 두 지역이 "해당지역"으로 매칭되는지 판정하는
`region_matches(notice_area, member_regions)`를 제공한다.

## Wiki pages (read these first, only these)
- (없음 — [no-wiki] 도메인 규칙. D18 근거)

## Inputs
- 바인딩 결정: D18(시/도 정규화 + 시 단위 예외맵), D19(사용자 정책 명시).
- 조사 대상: DB `notice.area_nm` 실제 값 분포. (조회: `uv run python -c "from src.db import SessionLocal, Notice; from sqlalchemy import select, func; s=SessionLocal(); print(s.execute(select(Notice.area_nm, func.count()).group_by(Notice.area_nm)).all())"` — DB 있을 때.)

## Steps
1. area_nm 실제 값을 표본조사(위 명령 또는 기존 테스트 샘플 `SAMPLE`)해 정규화 대상 목록 확정. 값이 "서울특별시/경기도/성남시…" 형태인지, 축약("서울/경기")인지 확인해 매핑표를 코드 상단 주석으로 문서화.
2. `src/regions.py` 생성:
   - `_CANON = {"서울특별시":"서울", "경기도":"경기", ...}`(조사 결과 기반) 시/도 정규화 맵.
   - `normalize_region(s: str) -> str`: 공백 제거 후 접미사("특별시/광역시/도") 제거 규칙 + `_CANON` 적용 → 정규형(시/도) 반환. 빈/None → `""`.
   - 시 단위 예외맵 `_CITY_ALIAS = {"성남":"성남", ...}`: 성남처럼 소득본거지 정책상 별도 매칭이 필요한 시는 정규형을 시 단위로 유지. (D18: 성남 등)
   - `region_matches(notice_area: str, member_regions: list[str]) -> bool`: `na = normalize_region(notice_area)`; member_regions 각각 normalize 후 `na`와 일치(또는 시/도 포함 규칙)하면 True. 빈 입력은 False(안전).
3. docstring에 D19(소득본거지 기반 매칭은 사용자 지정 정책, 공식 청약 규칙 아님) 명시.

## Deliverables
- `src/regions.py` (신규)
- `tests/test_regions.py` (신규)

## Verify
- `uv run pytest tests/test_regions.py -q 2>&1 | tail -20` 통과(순수함수라 DB 불요).
- 테스트: ① `normalize_region("서울특별시")=="서울"`, `"경기도"=="경기"` ② `region_matches("서울", ["서울","경기"]) is True` ③ 불일치: `region_matches("부산", ["서울"]) is False` ④ 경계: `region_matches("", [])`/`normalize_region(None)` 예외 없이 `""`/False ⑤ 예외맵: 성남 관련 케이스(조사 결과에 맞춰 1건).

## Out of scope
- judge_rank 통합(Task 09), 대시보드 필터(Task 11).
