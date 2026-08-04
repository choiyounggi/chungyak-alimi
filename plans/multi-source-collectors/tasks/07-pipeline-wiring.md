# Task 07: 파이프라인에 신규 4개 수집원 배선 + LH 보강을 native_id 기준으로 교정

## Objective
`run_batch()`가 6개 수집원(청약홈·LH·마이홈·HUG·SH·GH)을 각각 격리 실행해 저장하고,
LH 상세·공급 보강이 글로벌 ID가 아닌 원본 `PAN_ID`로 호출된다. 순위 판정이 청약홈
공고에만 적용된다.

## Wiki pages (read these first, only these)
- `wiki/backend/common/errors/exception-handling.md` — 소스 단위 격리에서 예외를 어디서 잡고 무엇을 로그할지
- `wiki/backend/common/jobs/scheduled-job-overlap.md` — 수집원이 6개로 늘어 배치 실행시간이 길어질 때의 겹침 대비
- `wiki/testing/quality/minimum-case-set.md` — 케이스 선정

## Inputs
- `src/pipeline.py` — `_safe()`(L31~37), `enrich_lh_supply()`(L40~69),
  `enrich_lh_detail()`(L105~142), `run_batch()`(L145~170).
- `src/scoring.py` — `judge_notice()`의 소스 분기(L241~247).
- Task 01 산출물: `src/db.py`의 `global_id()`, `AGENCY_BY_SOURCE`,
  `Notice.native_id`, `upsert_house_types(..., source=...)`.
- Task 03~06 산출물(정확한 이름):
  - `src/collectors/myhome.py` → `fetch_myhome_notices()`
  - `src/collectors/hug.py` → `fetch_hug_notices()`
  - `src/collectors/sh.py` → `fetch_sh_notices()`
  - `src/collectors/gh.py` → `fetch_gh_notices()`
- `tests/test_pipeline.py` — 기존 테스트 패턴.
- Decisions that bind you: D20(순위 판정 소스 분기 일반화), D22(첫 배치 backfill),
  D10(재시도 없음 — 실패는 `_safe`로 격리), D24(케이스).

## Steps
1. `src/pipeline.py` 상단 import에 4개를 추가한다(기존 collector import 인근, 알파벳 순):
   ```python
   from .collectors.gh import fetch_gh_notices
   from .collectors.hug import fetch_hug_notices
   from .collectors.myhome import fetch_myhome_notices
   from .collectors.sh import fetch_sh_notices
   ```
2. `enrich_lh_supply()`를 고친다. 글로벌 ID 전환 후 `n.pblanc_no`는 `"lh:2015..."`
   형태라 LH API에 그대로 넘기면 안 된다:
   - `pan_id=n.pblanc_no` → `pan_id=n.native_id or n.pblanc_no`
   - `upsert_house_types(supplies, session=session)` →
     `upsert_house_types(supplies, source="lh", session=session)`
   - **`supplies`의 `pblanc_no`는 건드리지 않는다.** `fetch_lh_supply()`가 이미
     `item.pblanc_no = pan_id`(원본 PAN_ID)를 넣어 반환하고(`src/collectors/lh.py:305`),
     `upsert_house_types`가 `source="lh"`로 `global_id()`를 붙인다. 여기서 또 대입하면
     이중 접두가 된다.
3. `enrich_lh_detail()`에서 `pan_id=n.pblanc_no` → `pan_id=n.native_id or n.pblanc_no`.
   `update(Notice).where(Notice.pblanc_no == n.pblanc_no)`는 글로벌 ID 그대로가 맞다(수정 없음).
4. `run_batch()`를 아래로 확장한다. 각 수집원은 `_safe()`로 감싸 하나가 죽어도 나머지가
   진행되게 한다(기존 규약 유지):
   ```python
   notices = _safe(fetch_apt_notices, "청약홈 공고 수집", [])
   house_types = _safe(fetch_apt_house_types, "청약홈 주택형 수집", [])
   lh_notices = _safe(fetch_lh_notices, "LH 공고 수집", [])
   myhome_notices = _safe(fetch_myhome_notices, "마이홈 공고 수집", [])
   hug_notices = _safe(fetch_hug_notices, "HUG 든든전세 수집", [])
   sh_notices = _safe(fetch_sh_notices, "SH 공고 수집", [])
   gh_notices = _safe(fetch_gh_notices, "GH 공고 수집", [])

   upsert_notices(notices, source="applyhome")
   upsert_house_types(house_types, source="applyhome")
   upsert_notices(lh_notices, source="lh")
   upsert_notices(myhome_notices, source="myhome")
   upsert_notices(hug_notices, source="hug")
   upsert_notices(sh_notices, source="sh")
   upsert_notices(gh_notices, source="gh")
   ```
   반환 dict에 건수를 추가한다:
   `"myhome_notices": len(myhome_notices), "hug_notices": len(hug_notices),`
   `"sh_notices": len(sh_notices), "gh_notices": len(gh_notices)`.
   기존 키(`collected`,`house_types`,`lh_notices`,`lh_enriched`,`lh_detailed`,
   `polygons`,`evaluated`,`matched`,`sent`)는 **이름을 바꾸지 않는다** — 운영 로그가 깨진다.
5. `src/scoring.py`의 소스 분기를 D20대로 일반화한다:
   ```python
   # 변경 전: if source == "lh" or "민영" not in dtl:
   # 변경 후:
   if source != "applyhome" or "민영" not in dtl:
   ```
   `judge_notice()`의 docstring도 한 줄 고친다:
   `"""공고 1건에 대한 종합 판정. 민영(청약홈)만 지원 — 그 외 소스는 별도 기준(순차제)."""`
6. `tests/test_pipeline.py`에 테스트를 추가한다. 실제 네트워크를 타지 않도록 4개 신규
   collector를 `monkeypatch`로 스텁한다(기존 테스트가 쓰는 방식을 따른다). 케이스(D24):
   - `test_run_batch_counts_all_sources`: 6개 수집원을 전부 스텁 →
     반환 dict에 `myhome_notices`/`hug_notices`/`sh_notices`/`gh_notices` 키가 있고
     값이 스텁 길이와 같다.
   - `test_one_collector_failure_does_not_stop_batch`: `fetch_gh_notices`가 예외를
     던지게 하면 `gh_notices == 0`이고 나머지 소스 건수는 정상이다.
   - `test_existing_result_keys_preserved`: 기존 9개 키가 그대로 있다(회귀 방지).
   - `tests/test_scoring.py`에 `test_non_applyhome_source_is_unsupported`:
     `source="myhome"`이고 `house_dtl_secd_nm="민영"`이어도
     `judge_notice(...)["supported"] is False`.
   - `tests/test_scoring.py`에 `test_applyhome_private_still_supported`(회귀):
     `source="applyhome"` + `"민영"` 포함 → `supported`가 False가 아니다.

## Deliverables
- `src/pipeline.py` (수정)
- `src/scoring.py` (수정 — 2줄)
- `tests/test_pipeline.py` (수정), `tests/test_scoring.py` (수정)

## Verify
- `./.venv/bin/pytest -q` → 전부 통과(기존 179개 + 신규).
- `./.venv/bin/ruff check src tests scripts` → 클린.
- `./.venv/bin/python -c "import src.pipeline"` → import 에러 없음.
- 테스트 중 실제 외부 요청 없음.

## Out of scope
- 웹 대시보드(08).
- `enrich_polygons()`를 PNU 기반으로 바꾸기(D28) — 후속.
- systemd 타이머 주기 변경·겹침 방지 도입 — 현재 배치 소요시간이 문제를 일으킨다는
  근거가 없다. 위키 페이지는 읽되, 이번 변경으로 실행시간이 눈에 띄게 늘면
  `plan.md`에 후속 과제로 적어둔다.
