"""config/profile.yaml → 회원 계정(Member + MemberProfile) 이관 시드(Task 07).

멀티유저 전환 전까지 단일 프로필(config/profile.yaml)로 돌던 순위 판정을,
회원 프로필 행으로 옮긴다. 멱등 — 이미 있는 회원은 다시 만들지 않는다.

사용:
    uv run python scripts/seed_member.py --email <이메일> --password <최초 비밀번호>
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 리포 루트를 import 경로에 추가

from src.db import SessionLocal, init_db  # noqa: E402
from src.members import (  # noqa: E402
    create_member,
    get_member_by_email,
    hash_password,
    update_profile,
)
from src.scoring import Profile, load_profile  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_PATH = "config/profile.yaml"


def flatten_profile(profile: Profile) -> dict:
    """scoring.Profile(중첩) → MemberProfile 컬럼 dict. members.profile_from_member 의 역방향."""
    return {
        "birth_date": profile.birth_date,
        "marriage_date": profile.marriage_date,
        "engaged": profile.engaged,
        "is_household_head": profile.is_household_head,
        "household_all_homeless": profile.household_all_homeless,
        "homeless_since": profile.homeless_since,
        "dependents": profile.dependents,
        "region": profile.region,
        "won_within_5y": profile.won_within_5y,
        "children_minor": profile.children_minor,
        "real_estate_manwon": profile.real_estate_manwon,
        # account/income/first_life 중첩 → 대응 컬럼
        "account_opened": profile.account.opened,
        "account_balance_manwon": profile.account.balance_manwon,
        "income_monthly_manwon": profile.income.monthly_manwon,
        "income_base_manwon": profile.income.base_manwon,
        "income_dual": profile.income.dual_income,
        "fl_ever_owned_house": profile.first_life.ever_owned_house,
        "fl_income_tax_5y": profile.first_life.income_tax_5y,
        "fl_currently_earning": profile.first_life.currently_earning,
    }


def _mask_email(email: str) -> str:
    """로그/출력용 마스킹 — 이메일은 PII 이므로 로컬파트를 남기지 않는다."""
    _, _, domain = (email or "").partition("@")
    return f"***@{domain}" if domain else "***"


def seed_member(
    email: str,
    password: str,
    *,
    session,
    profile_path: str = DEFAULT_PROFILE_PATH,
) -> tuple[int, bool, bool]:
    """회원을 만들고(없을 때만) profile.yaml 값을 프로필에 반영.

    반환: (member_id, 새로 생성했는지, 프로필을 반영했는지)
    """
    member = get_member_by_email(email, session=session)
    created = member is None
    if member is None:
        member = create_member(email, hash_password(password), session=session)
    member_id = member.id

    profile = load_profile(profile_path)
    if profile is None:
        logger.warning(
            "profile 파일이 없어 프로필 기본값을 유지합니다: %s — 웹 프로필 화면에서 입력하세요.",
            profile_path,
        )
        return member_id, created, False

    update_profile(member_id, flatten_profile(profile), session=session)
    return member_id, created, True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="config/profile.yaml 을 회원 계정(Member+MemberProfile)으로 이관한다."
    )
    parser.add_argument("--email", required=True, help="시드할 회원 이메일")
    parser.add_argument(
        "--password", required=True, help="최초 시드용 비밀번호(해시로만 저장되며 출력되지 않음)"
    )
    args = parser.parse_args(argv)

    init_db()
    with SessionLocal() as session:
        member_id, created, applied = seed_member(
            args.email, args.password, session=session, profile_path=DEFAULT_PROFILE_PATH
        )

    # 비밀번호는 어떤 경로로도 출력하지 않고, 이메일은 마스킹해 남긴다.
    print(
        f"{'생성' if created else '기존 계정 사용'}: member_id={member_id} "
        f"email={_mask_email(args.email)} profile={'반영' if applied else '기본값 유지'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
