from __future__ import annotations

import json
import logging
import sys

from sqlalchemy import exists, select, update

from .collectors.applyhome import fetch_apt_house_types, fetch_apt_notices
from .collectors.lh import fetch_lh_detail, fetch_lh_notices, fetch_lh_supply
from .collectors.vworld import fetch_parcel_polygon
from .config import settings
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

logger = logging.getLogger(__name__)


def _safe(fn, label: str, default):
    """collector 하나가 실패해도 배치 전체를 중단하지 않는다(부분 수집 허용)."""
    try:
        return fn()
    except Exception:
        logger.exception("%s 실패 — 이 소스는 건너뜀", label)
        return default


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


def enrich_polygons() -> int:
    """매칭 공고의 주소 → V-World 필지 폴리곤을 raw['_polygon']에 저장. 폴리곤 획득 수 반환."""
    if not settings.vworld_key:
        return 0
    added = 0
    with SessionLocal() as session:
        q = (
            select(Notice)
            .join(MatchResult, Notice.pblanc_no == MatchResult.pblanc_no)
            .where(MatchResult.matched.is_(True))
        )
        for n in session.scalars(q).all():
            raw = n.raw or {}
            if "_polygon" in raw:  # 이미 시도함(빈 배열이면 없음)
                continue
            addr = raw.get("HSSPLY_ADRES") or n.hsslpy_adres
            if not addr:
                continue
            poly = fetch_parcel_polygon(addr)
            session.execute(
                update(Notice)
                .where(Notice.pblanc_no == n.pblanc_no)
                .values(raw={**raw, "_polygon": poly or []})
            )
            if poly:
                added += 1
        session.commit()
    return added


def enrich_lh_detail() -> int:
    """매칭된 LH 공고의 상세(주소·일정·서류제출·공고전문)를 raw에 병합. 처리 건수 반환."""
    added = 0
    with SessionLocal() as session:
        q = (
            select(Notice)
            .join(MatchResult, Notice.pblanc_no == MatchResult.pblanc_no)
            .where(MatchResult.matched.is_(True), Notice.source == "lh")
        )
        for n in session.scalars(q).all():
            r = n.raw or {}
            if r.get("_lh_detail"):  # 이미 보강됨
                continue
            d = fetch_lh_detail(
                pan_id=n.pblanc_no,
                ccr=r.get("CCR_CNNT_SYS_DS_CD"),
                spl=r.get("SPL_INF_TP_CD"),
                upp=r.get("UPP_AIS_TP_CD"),
                ais=r.get("AIS_TP_CD"),
            )
            if d:
                session.execute(
                    update(Notice)
                    .where(Notice.pblanc_no == n.pblanc_no)
                    .values(raw={**r, "_lh_detail": d}, hsslpy_adres=d.get("adres") or n.hsslpy_adres)
                )
                added += 1
        session.commit()
    return added


def run_batch(*, notify: bool = True) -> dict:
    """수집 → 저장 → 평가 → (알림). 배치 1회."""
    init_db()
    notices = _safe(fetch_apt_notices, "청약홈 공고 수집", [])
    house_types = _safe(fetch_apt_house_types, "청약홈 주택형 수집", [])
    lh_notices = _safe(fetch_lh_notices, "LH 공고 수집", [])
    upsert_notices(notices, source="applyhome")
    upsert_house_types(house_types)
    upsert_notices(lh_notices, source="lh")
    total, matched = evaluate_all(load_filter_config())
    lh_enriched = enrich_lh_supply()  # 매칭된 LH의 면적·세대수 보강
    lh_detailed = enrich_lh_detail()  # 매칭된 LH의 주소·일정·공고전문 보강
    polygons = enrich_polygons()      # 매칭 공고의 필지 폴리곤(V-World)
    sent = notify_new_matches() if notify else 0
    return {
        "collected": len(notices),
        "house_types": len(house_types),
        "lh_notices": len(lh_notices),
        "lh_enriched": lh_enriched,
        "lh_detailed": lh_detailed,
        "polygons": polygons,
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
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
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
