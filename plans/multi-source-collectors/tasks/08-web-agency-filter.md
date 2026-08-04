# Task 08: 대시보드에 기관 필터 칩 + 카드 기관 배지 추가

## Objective
대시보드에 `LH / SH / GH / HUG / 기타` 기관 칩이 뜨고, 클릭하면 기존 다중선택 칩 필터와
같은 규칙(종류 안 OR, 종류 간 AND)으로 카드와 지도 마커가 함께 걸러진다. 각 카드에
기관 배지가 표시된다.

## Wiki pages (read these first, only these)
- `wiki/frontend/security/xss-safe-rendering.md` — 서드파티에서 온 값을 DOM에 넣을 때의 규칙
- `wiki/frontend/data-fetching/race-conditions.md` — 필터 전환 시 재조회를 하지 않아 레이스를 없애는 기존 구조 유지

## Inputs
- `src/web/templates/index.html` — 칩 렌더 블록(L45~57), 필터 JS의
  `matchesType()`(L113~125), `matches()`/`renderList()`(L127~145),
  `window.chungyakChipMatch`/`chungyakApplyList` 배선(L148~152), 푸터(L100).
- `src/web/templates/_macros.html` — `notice_card` 매크로의 `data-*` 속성 블록(L5~11).
  `n`은 `Notice` ORM 행이라 Task 01이 추가한 `n.agency`를 그대로 읽을 수 있다.
- `src/web/app.py` — `index` 라우트의 템플릿 컨텍스트.
- `src/db.py`의 `AGENCIES` 상수(Task 01 산출물).
- `tests/test_index_template.py` — 기존 렌더 어서션 패턴.
- Decisions that bind you: D7(기관 5종 값 집합), D21(기존 칩 구조 재사용·서버 재조회 없음).

## Steps
1. `src/web/app.py`의 `index` 라우트(L341~351) 컨텍스트 dict(L350)에
   `"agencies": AGENCIES`를 추가하고, `from .db import ...` 목록에 `AGENCIES`를 넣는다.
   `bookmarks_page` 라우트(L354~)는 기관 칩을 쓰지 않으므로 손대지 않는다.
2. `src/web/templates/_macros.html`의 `notice_card` 매크로에서:
   - `data-*` 블록에 `data-agency="{{ n.agency or '기타' }}"`를 추가한다(L5~11 인근).
   - 카드 안, 기존 공고명 근처에 기관 배지를 넣는다. Jinja2 자동이스케이프가 켜져 있으므로
     `{{ }}`로 그대로 출력하면 된다(`|safe` 금지):
     ```html
     <span class="badge badge--agency">{{ n.agency or '기타' }}</span>
     ```
3. `src/web/templates/index.html`의 칩 블록에 기관 칩 행을 추가한다. 기존
   `data-ftype="area"` 칩 바로 앞에 둔다(기관 → 지역 → 유형 → 특공 → 순위 순):
   ```html
   {% for a in agencies %}
   <button type="button" class="chip" data-ftype="agency" data-fval="{{ a }}" aria-pressed="false">{{ a }}</button>
   {% endfor %}
   ```
4. 같은 파일의 `matchesType()`에 한 줄 추가한다(`area` 분기 옆):
   ```javascript
   if (type === "agency") return vals.indexOf(card.dataset.agency) !== -1;
   ```
   `matches()`·`renderList()`·`chungyakApplyList` 배선은 **건드리지 않는다** — 기존
   구조가 새 type을 자동으로 처리하고, 재조회가 없으므로 레이스도 생기지 않는다(D21).
5. `index.html` 상단 `<style>`에 배지 스타일을 추가한다(기존 `.chip` 규칙 인근,
   `base.html`은 수정하지 않는다):
   ```css
   .badge--agency{display:inline-block;padding:1px 7px;border-radius:10px;
     font-size:11px;font-weight:600;background:#eef4ff;color:#2b5fd9;margin-right:6px}
   ```
   `_macros.html`은 `bookmarks.html`에서도 쓰이므로, 배지가 그 페이지에서 스타일 없이
   나오지 않도록 같은 규칙을 `bookmarks.html`의 `{% block head %}` 안 `<style>`(L4~)에도 넣는다.
6. 푸터 문구를 사실에 맞게 고친다(`index.html` L100):
   `공공 오픈API(청약홈/LH) 기반` → `공공 오픈API(청약홈·LH·마이홈·HUG) + 공식 포털(SH·GH) 기반`
7. `tests/test_index_template.py`에 테스트를 추가한다. 케이스:
   - `test_agency_chips_rendered`: 렌더 결과에 `data-ftype="agency"` 칩이 5개 있고
     `LH`,`SH`,`GH`,`HUG`,`기타`가 모두 나온다.
   - `test_card_has_agency_dataset`: `agency="SH"`인 공고 카드에
     `data-agency="SH"`가 실린다.
   - `test_agency_badge_rendered`: 카드에 `badge--agency` 배지와 기관명이 나온다.
   - `test_agency_none_falls_back_to_etc`(경계): `agency=None`인 공고는
     `data-agency="기타"`로 렌더된다.
   - `test_matches_type_handles_agency`: 렌더된 스크립트에
     `type === "agency"` 분기 문자열이 포함된다.

## Deliverables
- `src/web/app.py` (수정 — import 1줄 + 컨텍스트 1줄)
- `src/web/templates/_macros.html` (수정)
- `src/web/templates/index.html` (수정), `src/web/templates/bookmarks.html` (수정 — CSS 1블록)
- `tests/test_index_template.py` (수정 — 테스트 5개 추가)

## Verify
- `./.venv/bin/pytest tests/test_index_template.py tests/test_base_template.py tests/test_bookmarks_template.py -q` → 전부 통과.
- `./.venv/bin/pytest -q` → 전체 회귀 없음.
- `./.venv/bin/ruff check src tests` → 클린.
- `grep -n "|safe" src/web/templates/_macros.html` → 기관 배지에는 없어야 한다.

## Out of scope
- 상세 페이지(`detail.html`)의 기관 표시.
- 지도 마커 아이콘을 기관별로 다르게 하기 — 기존 마커 토글만 재사용한다.
- `base.html` 수정 — 페이지 로컬 스타일로 끝낸다(기존 관례).
- 기관별 통계·집계 UI.
