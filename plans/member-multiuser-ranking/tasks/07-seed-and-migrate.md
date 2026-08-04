# Task 07: profile.yaml→영기 계정 시드 + 기존 북마크 이관

## Objective
`scripts/seed_member.py` 실행 시 기존 `config/profile.yaml` 값이 영기 계정
(`Member` + `MemberProfile`)으로 이관되고, 기존 전역 북마크가 그 계정으로 재지정된다.
멱등하게 재실행 가능.

## Wiki pages (read these first, only these)
- wiki/security/data/pii-handling.md — 시드 이메일/자격증명 취급, 로그에 비밀번호/PII 미출력
- wiki/security/authn/password-storage.md — 시드 비밀번호도 argon2id 해시로 저장

## Inputs
- Task 02/04 산출물: `create_member`, `get_member_by_email`, `update_profile`, `hash_password`.
- Task 03 산출물: `migrate_global_bookmarks_to_member(member_id, *, session)`.
- `src/scoring.py`: `load_profile(path)` → `Profile | None`(기존 profile.yaml 로더).
- 기존 `scripts/seed_demo.py`의 `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` 션 패턴(스크립트 임포트 경로).
- 바인딩 결정: D3(해시), D11(이관).

## Steps
1. `scripts/seed_member.py` 생성. sys.path 션 추가 후 `from src.db import SessionLocal, init_db, migrate_global_bookmarks_to_member` / `from src.members import create_member, get_member_by_email, update_profile, hash_password` / `from src.scoring import load_profile`.
2. 인자: `--email`(필수), `--password`(필수 — 최초 시드용). `argparse`.
3. 동작:
   - `init_db()`.
   - 이미 있으면(get_member_by_email) 그 회원 사용, 없으면 `create_member(email, hash_password(password))`.
   - `load_profile()`로 기존 profile.yaml 읽어(있으면) `update_profile(member_id, {...})`로 대응 필드 채움. profile.yaml 없으면 프로필은 기본값 유지(경고 로그).
   - `migrate_global_bookmarks_to_member(member_id)` 호출.
   - 요약 출력(비밀번호는 절대 출력하지 않음).
4. 멱등: 재실행 시 회원 중복 생성 안 함, 북마크 이관 no-op.

## Deliverables
- `scripts/seed_member.py` (신규)
- `tests/test_seed_member.py` (신규)

## Verify
- `uv run pytest tests/test_seed_member.py -q 2>&1 | tail -20` 통과.
- 테스트(`_db_available` 게이트, 시드 함수 단위 or subprocess): ① 시드 1회 → 회원+프로필 존재, profile.yaml 값 반영 ② 재실행 멱등(회원 1건 유지) ③ 기존 전역 북마크가 있으면 이관됨 ④ 경계: profile.yaml 없을 때도 회원은 생성되고 예외 없음.
- 수동(배포용, 선택): `uv run python scripts/seed_member.py --email <나> --password <임시>` 로 로컬 DB에 1회 실행 가능함을 확인.

## Out of scope
- 웹 회원가입 폼(Task 05/06), 프로필 수정 UI(Task 10).
