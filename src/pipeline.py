from __future__ import annotations

import json
import sys

from .collectors.applyhome import fetch_apt_house_types, fetch_apt_notices
from .db import (
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


def run_batch(*, notify: bool = True) -> dict:
    """수집 → 저장 → 평가 → (알림). 배치 1회."""
    init_db()
    notices = fetch_apt_notices()
    house_types = fetch_apt_house_types()
    upsert_notices(notices)
    upsert_house_types(house_types)
    total, matched = evaluate_all(load_filter_config())
    sent = notify_new_matches() if notify else 0
    return {
        "collected": len(notices),
        "house_types": len(house_types),
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
