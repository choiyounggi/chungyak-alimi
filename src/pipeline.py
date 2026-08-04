from __future__ import annotations

import json
import logging
import sys

from sqlalchemy import exists, select, update

from .collectors.applyhome import fetch_apt_house_types, fetch_apt_notices
from .collectors.gh import fetch_gh_notices
from .collectors.hug import fetch_hug_notices
from .collectors.lh import fetch_lh_detail, fetch_lh_notices, fetch_lh_supply
from .collectors.myhome import fetch_myhome_notices
from .collectors.sh import fetch_sh_notices
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
            try:
                supplies = fetch_lh_supply(
                    pan_id=n.native_id or n.pblanc_no,
                    ccr=r.get("CCR_CNNT_SYS_DS_CD"),
                    spl=r.get("SPL_INF_TP_CD"),
                    upp=r.get("UPP_AIS_TP_CD"),
                    ais=r.get("AIS_TP_CD"),
                )
                if supplies:
                    upsert_house_types(supplies, source="lh", session=session)
                    added += len(supplies)
            except Exception:
                logger.exception("LH 공급정보 보강 실패(pblanc_no=%s) — 건너뜀", n.pblanc_no)
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
            try:
                poly = fetch_parcel_polygon(addr)
                session.execute(
                    update(Notice)
                    .where(Notice.pblanc_no == n.pblanc_no)
                    .values(raw={**raw, "_polygon": poly or []})
                )
                if poly:
                    added += 1
            except Exception:
                logger.exception("폴리곤 보강 실패(pblanc_no=%s) — 건너뜀", n.pblanc_no)
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
            d0 = r.get("_lh_detail")
            # 이미 보강됨 — 단 구버전(images 키 없음)과 뷰어 URL 세대(lhImageView 미해석)는 1회 재보강
            if (
                d0
                and "images" in d0
                and not any("lhImageView" in (im.get("url") or "") for im in d0["images"])
            ):
                continue
            try:
                d = fetch_lh_detail(
                    pan_id=n.native_id or n.pblanc_no,
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
            except Exception:
                logger.exception("LH 상세 보강 실패(pblanc_no=%s) — 건너뜀", n.pblanc_no)
        session.commit()
    return added


def run_batch(*, notify: bool = True) -> dict:
    """수집 → 저장 → 평가 → (알림). 배치 1회."""
    init_db()
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
    total, matched = evaluate_all(load_filter_config())
    # 보강 단계도 소스별 격리 — 외부 API 이상이 배치 전체를 중단하지 않게
    lh_enriched = _safe(enrich_lh_supply, "LH 공급정보 보강", 0)
    lh_detailed = _safe(enrich_lh_detail, "LH 상세 보강", 0)
    polygons = _safe(enrich_polygons, "필지 폴리곤 보강", 0)
    sent = notify_new_matches() if notify else 0
    return {
        "collected": len(notices),
        "house_types": len(house_types),
        "lh_notices": len(lh_notices),
        "myhome_notices": len(myhome_notices),
        "hug_notices": len(hug_notices),
        "sh_notices": len(sh_notices),
        "gh_notices": len(gh_notices),
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


class SecretRedactingFormatter(logging.Formatter):
    """포맷된 로그 문자열에서 API 키를 가린다.

    httpx 예외 메시지는 요청 URL을 쿼리스트링째 담는다
    (`... for url '...?API_KEY=<키>'`). `_safe()` 가 이를 `logger.exception()` 으로
    남기므로 traceback 까지 저널에 찍힌다 — logging.Filter 는 record 만 보고
    traceback 문자열은 못 건드리므로, 최종 포맷 단계에서 지운다.
    """

    def format(self, record: logging.LogRecord) -> str:
        out = super().format(record)
        for secret in (settings.odcloud_api_key, settings.hug_api_key, settings.vworld_key):
            if secret:  # 빈 문자열을 replace 하면 모든 문자 사이에 마스크가 끼어든다
                out = out.replace(secret, "***")
        return out


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        SecretRedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # httpx는 요청 URL 전체(쿼리스트링의 API 키 포함)를 INFO로 남기므로 저널 노출 차단
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
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
