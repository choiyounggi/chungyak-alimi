from __future__ import annotations

import json
import sys

from sqlalchemy import exists, select

from .collectors.applyhome import fetch_apt_house_types, fetch_apt_notices
from .collectors.lh import fetch_lh_notices, fetch_lh_supply
from .db import (
    MatchResult,
    Notice,
    NoticeHouseType,
    SessionLocal,
    evaluate_all,
    init_db,
    mark_notified,
    pending_notifications,
    upsert_house_types,
    upsert_notices,
)
from .filters import load_filter_config
from .notify import notify_new_matches


def enrich_lh_supply() -> int:
    """매칭된 LH 공고의 공급정보(면적·세대수)를 채운다. 처리한 주택형 수 반환."""
    added = 0
    with SessionLocal() as session:
        q = (
            select(Notice)
            .join(MatchResult, Notice.pblanc_no == MatchResult.pblanc_no)
            .where(MatchResult.matched.is_(True), Notice.source == "lh")
        )
        for n in session.scalars(q).all():
            already = session.scalar(
                select(exists().where(NoticeHouseType.pblanc_no == n.pblanc_no))
            )
            if already:
                continue
            r = n.raw or {}
            supplies = fetch_lh_supply(
                pan_id=n.pblanc_no,
                ccr=r.get("CCR_CNNT_SYS_DS_CD"),
                spl=r.get("SPL_INF_TP_CD"),
                upp=r.get("UPP_AIS_TP_CD"),
                ais=r.get("AIS_TP_CD"),
            )
            if supplies:
                upsert_house_types(supplies, session=session)
                added += len(supplies)
    return added


def run_batch(*, notify: bool = True) -> dict:
    """수집 → 저장 → 평가 → (알림). 배치 1회."""
    init_db()
    notices = fetch_apt_notices()
    house_types = fetch_apt_house_types()
    lh_notices = fetch_lh_notices()
    upsert_notices(notices, source="applyhome")
    upsert_house_types(house_types)
    upsert_notices(lh_notices, source="lh")
    total, matched = evaluate_all(load_filter_config())
    lh_enriched = enrich_lh_supply()  # 매칭된 LH의 면적·세대수 보강
    sent = notify_new_matches() if notify else 0
    return {
        "collected": len(notices),
        "house_types": len(house_types),
        "lh_notices": len(lh_notices),
        "lh_enriched": lh_enriched,
        "evaluated": total,
        "matched": matched,
        "sent": sent,
    }


def backfill_notified() -> int:
    """첫 배포용: 현재 매칭을 '발송 완료'로 기록해 재알림을 막는다."""
    with SessionLocal() as session:
        pending = pending_notifications(session=session)
        for n in pending:
            mark_notified(n.pblanc_no, session=session)
        return len(pending)


def main() -> None:
    if "--backfill" in sys.argv:
        result = run_batch(notify=False)
        result["backfilled"] = backfill_notified()
    elif "--no-notify" in sys.argv:
        result = run_batch(notify=False)
    else:
        result = run_batch(notify=True)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
