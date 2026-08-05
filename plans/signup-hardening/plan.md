# signup-hardening

Goal: 회원가입을 **계정 생성 + 3스텝 온보딩**으로 확장한다. 이메일 유효성과
KISA 기준 비밀번호 정책(+비밀번호 확인, 마스킹 토글)을 가입 경계에서 강제하고,
온보딩에서 받은 프로필(나이·자차/차량가액·주택보유·세대주 자가·지역별 거주기간·
청약통장 납입횟수/예치금·선호전형 복수선택·신혼 인정기간·예비신혼 각자 정보)로
**민영 + 국민주택 1·2순위**를 목록에서 판별해 보여준다.

Acceptance:
- `POST /register` 가 (1) 유효하지 않은 이메일, (2) KISA 정책 미달 비밀번호,
  (3) 비밀번호 확인 불일치를 각각 필드 인라인 에러로 거부한다.
- 회원가입/로그인 폼의 비밀번호 입력칸 우측 눈 아이콘으로 마스킹을 토글할 수 있고,
  토글 상태가 `aria-pressed` / `aria-label` 로 스크린리더에 전달된다.
- 가입 성공 시 `/onboarding/1` 로 이동하고, 3스텝을 마치면 `/` 로 이동한다.
  중간 이탈 후 재로그인하면 미완성 배너로 남은 스텝을 이어갈 수 있다.
- 신혼부부를 선택했는데 혼인신고일이 7년을 넘으면 저장이 거부된다(필드 에러).
- 예비신혼부부를 선택하면 두 사람 각각의 (부모동거/독립, 자가 보유, 거주지,
  소득본거지)를 받고, 어느 한쪽이라도 자가 보유면 예비신혼 특공 부적격으로 표시된다.
- 목록(대시보드)이 공고 유형에 따라 민영은 `judge_rank`, 국민주택은
  `judge_rank_public`(가입기간 + 납입횟수 + 무주택 + 규제지역 세대주) 결과로
  1순위/2순위를 표시한다.
- 신규 컬럼이 **기존 배포 DB에 멱등 ALTER 로** 반영되고, 기존 회원 행은 기본값으로
  살아남는다(기존 비밀번호는 소급 검증하지 않는다).

Stack: Python 3 · FastAPI · Jinja2 · SQLAlchemy 2.0 · PostgreSQL(psycopg3) ·
pydantic v2 · uv. 테스트 pytest(`_db_available` 게이트).

선행 계획: `plans/member-multiuser-ranking/plan.md` (회원/프로필/북마크/온더플라이 순위).
이 계획은 그 위에 **가입 경계 강화 + 프로필 확장 + 국민주택 순위**를 얹는다.

## Decisions

| # | Decision | Choice | Basis |
|---|----------|--------|-------|
| E1 | 이메일 검증 | 정규식 대신 `pydantic.EmailStr`(email-validator, `check_deliverability=False`). 문법·도메인 형태만 보고 **MX/SMTP 조회는 하지 않는다** — 가입 응답에 외부 DNS 왕복을 넣지 않는다 | security-input-validation-at-trust-boundaries |
| E2 | 이메일 정규화 | 저장은 기존대로 `strip().lower()`. **plus-address(`a+b@`)/dot 정규화는 하지 않는다** — 다른 사람의 주소를 같은 계정으로 접는 오탐 위험 | (도메인 판단) |
| E3 | 새 의존성 | `pydantic[email]` (= email-validator) 를 `pyproject.toml` 에 추가. 그 외 신규 의존성 없음(KISA 검증은 표준 라이브러리로 자체 구현) | — |
| P1 | KISA 비밀번호 조합 규칙 | 문자종류(영대문자/영소문자/숫자/특수문자) **2종 조합 → 10자 이상**, **3종 이상 조합 → 8자 이상**. 1종 단독은 길이 무관 불가 | KISA 「암호 이용 안내서」 패스워드 선택 및 이용 기준 |
| P2 | KISA 회피 패턴 | ① 동일 문자 3연속(`aaa`) ② 사전순 연속 3자(`abc`/`cba`/`123`/`321`) ③ 키보드 인접 3연속(`qwe`/`asd`/`zxc` 및 역순) ④ 이메일 로컬파트 포함(3자 이상 부분일치) ⑤ 내장 취약 비밀번호 목록 일치 — 각각 개별 위반 사유로 반환 | 동상 |
| P3 | 길이 상한 | 기존 `MAX_PASSWORD_LEN=128` 유지(argon2 메모리하드 함수 DoS 방지). 정책 검증은 해싱 **전에** 수행 | 기존 D3 |
| P4 | 소급 적용 범위 | `POST /register` 에서만 강제. `POST /login` 은 길이 상한만 본다 — 기존 계정을 잠그지 않는다 | 사용자 결정(2026-08-05) |
| P5 | 위반 사유 노출 | 어떤 규칙을 어겼는지 **모두** 사용자에게 보여준다(정책은 공개 정보이고, 숨기면 고칠 수가 없다). 반면 로그인 실패 문구는 기존대로 계정 존재를 흘리지 않는 고정 문구 유지 | frontend-forms-validation-timing / 기존 D15 |
| P6 | 정책 모듈 위치 | `src/password_policy.py` 순수함수(`validate_password(password, *, email=None) -> list[str]`). DB·요청 객체 의존 없음 → 라우트/스크립트/테스트가 모두 같은 함수를 쓴다 | backend-python-boundaries-runtime-validation |
| U1 | 마스킹 토글 | 인풋 우측 `<button type="button">` + `aria-pressed` + `aria-label` 전환. `type="password"↔"text"` 만 바꾸고 값은 건드리지 않는다. JS 미동작 시에도 마스킹 상태로 정상 입력 가능(점진적 향상) | frontend-accessibility-interactive-elements |
| U2 | 토글 스코프 | register(비밀번호·비밀번호확인), login(비밀번호). 아이콘은 `base.html` SVG 심볼(`#i-eye`/`#i-eye-off`)로 한 번만 정의 | frontend-security-xss-safe-rendering |
| U3 | 실시간 규칙 표시 | 입력 중 규칙 체크리스트를 갱신하되 **판정 권한은 서버**(제출 시 서버 재검증이 최종). 클라이언트는 같은 규칙의 힌트일 뿐 | frontend-forms-validation-timing |
| O1 | 온보딩 진행상태 | `member_profile.onboarding_step SMALLINT NOT NULL DEFAULT 0`(0=미시작 … 3=완료). 세션이 아닌 DB에 둔다 — 기기를 바꿔도 이어진다 | frontend-state-client-vs-server-state |
| O2 | 온보딩 라우터 분리 | `src/web/onboarding.py` 를 새 `APIRouter` 로 두고 `app.py` 는 `include_router` 한 줄만 추가. 590줄짜리 `app.py` 를 더 키우지 않는다 | maintainability(기존 auth.py 선례) |
| O3 | 스텝 분할 | 1=기본·세대(생년월일/세대유형/세대주/무주택/부양가족/자녀/혼인신고일), 2=자산·통장·소득(자차보유·차량가액/부동산가액/세대주 자가/통장 가입일·납입횟수·예치금/소득), 3=지역 거주기간·선호전형·예비신혼 파트너 | 사용자 결정(2026-08-05) |
| O4 | 부분 저장 | 각 스텝 POST 는 **그 스텝의 필드만** 검증·저장하고 `onboarding_step` 을 전진시킨다. 되돌아가기 허용(스텝 번호는 낮출 수 있고 완료 플래그는 내리지 않는다) | backend-common-api-design-error-responses |
| O5 | 미완성 유도 | `onboarding_step < 3` 인 회원이 `/` 에 오면 상단 배너로 이어하기 링크. **차단은 하지 않는다** — 프로필 없이도 목록은 볼 수 있어야 한다 | (제품 판단) |
| O6 | 폼 검증 재사용 | 기존 `ProfileForm` 을 스텝별 부분 모델로 쪼개되(`OnboardingStep1/2/3`), 필드 타입 애너테이션(`_OptDate`/`_Count`/`_Regions`)은 **재정의하지 않고 import** 한다 | DRY / 기존 D16 |
| D1 | 신규 컬럼 | `owns_car` BOOL, `account_payment_count` INT, `residence_history` JSONB, `preferred_types` JSONB, `partners` JSONB, `onboarding_step` SMALLINT — 모두 `NOT NULL` + `server_default` | databases-schema-design-nullability-and-defaults |
| D2 | 거주기간 표현 | `residence_history` = `[{"region": str, "since": "YYYY-MM-DD"}]`(JSONB). 기간을 년수로 저장하지 않는다 — 시간이 지나면 틀리는 값을 저장하면 안 된다. 조회 시 `today - since` 로 계산 | databases-schema-design-column-data-types |
| D3 | `residence_regions` 관계 | 기존 컬럼은 **유지**하고 `residence_history` 저장 시 `[h.region …]` 로 **파생 동기화**한다. `region_matches`/대시보드가 쓰는 계약을 깨지 않기 위한 의도적 비정규화이며, 쓰기 경로는 `update_profile` 한 곳뿐이다 | (마이그레이션 안전) |
| D4 | 선호전형 다중선택 | `preferred_types` JSONB `list[str]`, 허용값 `newlywed/pre_newlywed/youth/special/general`. 기존 단일 `household_type`(CHECK)은 **세대 실태**로 남기고, 선호는 별도 컬럼 — 둘은 다른 개념이다 | databases-schema-design-column-data-types |
| D5 | 예비신혼 파트너 | `partners` JSONB `[{label, lives_with_parents: bool, owns_home: bool, residence_region: str, income_base_region: str}]` 최대 2개. 별도 테이블을 만들지 않는다 — 항상 프로필과 함께 통째로 읽고 쓰며 개별 조회·조인이 없다 | databases-schema-design-column-data-types |
| D6 | 마이그레이션 | `init_db()` 의 기존 경량 ALTER 목록에 `ADD COLUMN IF NOT EXISTS` 로 추가(멱등). Alembic 도입하지 않음 — 기존 방식과 일관 | 기존 관례 |
| R1 | 국민주택 순위 | `judge_rank_public()` 신설: 가입기간 ≥ (규제 24개월 / 수도권 12개월 / 그 외 6개월), 납입횟수 ≥ (규제 24회 / 수도권 12회 / 그 외 6회), 무주택 세대구성원, 규제지역이면 세대주. 하나라도 미달이면 2순위 | 주택공급에 관한 규칙 제27조·제28조 (2026-08 확인, 상수 1곳 집중) |
| R2 | 유형 분기 | `judge_notice()` 가 공고를 민영/국민으로 나눠 각각 `judge_rank`/`judge_rank_public` 을 부른다. 판별 불가면 기존대로 `supported=False` | (기존 구조 확장) |
| R3 | 거주기간 요건 | `residence_years_in(profile, region, today)` 순수함수. 규제지역 공고는 해당지역 우선공급에 거주기간(기본 2년)을 요구하므로 **미달 시 사유에만 표기**하고 순위는 낮추지 않는다 — 기존 D19(지역은 순위 요건 아님)와 같은 원칙 | 기존 D19 |
| R4 | 신혼 인정기간 | 혼인신고일 기준 7년 초과면 `judge_newlywed` 이 기존대로 부적격. **추가로 온보딩 저장 시점에도** 같은 규칙으로 폼 에러를 낸다(입력 순간에 알려주는 편이 낫다) | 기존 scoring 규칙 재사용 |
| R5 | 예비신혼 자가 검증 | `partners` 중 하나라도 `owns_home` 이면 예비신혼 특공 부적격 사유 추가. `household_all_homeless` 와 별개로 본다 — 예비신혼은 아직 한 세대가 아니다 | (도메인 정책, docstring 명시) |
| R6 | 차량가액 활용 | ~~공공 특별공급 자산요건에 쓴다~~ → **철회(2026-08-05).** 차량가액·자차보유는 **수집·표시만** 하고 1·2순위 판정에 넣지 않는다. 이유: ① 사용자 요청은 1·2순위 판별이고 자산요건은 특별공급 *자격* 판정이라 별건, ② 현행 자산요건 금액(부동산·자동차 상한)을 이 작업 중 **1차 출처로 검증할 수 없어** 상수에 "확인" 주석을 달면 거짓이 된다. 필요해지면 별도 태스크로 근거와 함께 도입 | t03 계획 단계에서 워커가 제기, 오케스트레이터 확정 |
| T1 | 테스트 | 파일당 정상 + 에러 + 경계 ≥3, DB 필요 테스트는 `_db_available` 게이트. 정책·순위 순수함수는 DB 없이 테스트 | testing-quality-minimum-case-set |

## Task order

| Task | 개요 | Files (배타) | Depends on | Wave |
|------|------|--------------|-----------|------|
| 01 | 프로필 스키마 확장 + Profile 필드 + 어댑터 | `src/db.py`, `src/members.py`, `src/scoring.py`(Profile 클래스 한정), `tests/test_profile_schema_ext.py` | — | 1 |
| 02 | KISA 비밀번호 정책 모듈 | `src/password_policy.py`, `tests/test_password_policy.py` | — | 1 |
| 03 | 국민주택 순위 + 거주기간/예비신혼 검증 | `src/scoring.py`(판정 함수), `tests/test_scoring_public.py` | 01 | 2 |
| 04 | 가입 경계 강화 + 마스킹 토글 | `src/web/auth.py`, `templates/register.html`, `templates/login.html`, `templates/base.html`, `pyproject.toml`, `tests/test_auth_register_policy.py` | 02 | 2 |
| 05 | 3스텝 온보딩 라우터 + 템플릿 | `src/web/onboarding.py`(신규), `templates/onboarding_*.html`, `src/web/app.py`(include_router + 배너), `tests/test_onboarding.py` | 01, 04 | 3 |
| 06 | 목록 순위 반영(민영/국민 분기) | `src/web/app.py`(member_dashboard), `templates/index.html`, `tests/test_dashboard_rank_public.py` | 03, 05 | 4 |

### Wave 구성 근거

- **Wave 1** (01 ∥ 02): 01은 DB/어댑터, 02는 의존성 0인 순수 모듈. 파일 교집합 없음.
  01이 `scoring.py` 의 **Profile 클래스 필드만** 건드리고 판정 함수는 손대지 않는 것이
  03과의 분리선이다.
- **Wave 2** (03 ∥ 04): 03은 `scoring.py` 판정 함수, 04는 web/auth + 템플릿 + pyproject.
  교집합 없음. 04는 02의 `validate_password` 시그니처를, 03은 01의 Profile 필드를 소비.
- **Wave 3** (05 단독): `app.py` 를 처음 건드리는 태스크. 04가 만든 `/onboarding/1`
  리다이렉트 타깃을 실제로 구현한다.
- **Wave 4** (06 단독): `app.py` 를 두 번째로 건드리므로 05와 반드시 순차.

### 계약(선행 인터페이스)

Wave 2 이후 태스크에 주입할 시그니처:

```python
# Task 02 → Task 04
def validate_password(password: str, *, email: str | None = None) -> list[str]: ...
# 빈 리스트 = 통과. 각 원소는 사용자에게 그대로 보여줄 한국어 위반 사유.

# Task 01 → Task 03, 05
class Profile(BaseModel):          # src/scoring.py, 기존 필드 + 아래 추가
    owns_car: bool = False
    account_payment_count: int = 0
    residence_history: list[ResidencePeriod] = []
    preferred_types: list[str] = []
    partners: list[PartnerInfo] = []

class ResidencePeriod(BaseModel):  # src/scoring.py
    region: str = ""
    since: date | None = None

class PartnerInfo(BaseModel):      # src/scoring.py
    label: str = ""
    lives_with_parents: bool = False
    owns_home: bool = False
    residence_region: str = ""
    income_base_region: str = ""

# Task 03 → Task 06
def judge_rank_public(notice, p: Profile, today: date | None = None) -> dict: ...
# {"rank": "1순위"|"2순위", "regulated": bool, "reasons": list[str], "in_area": bool | None}
```
