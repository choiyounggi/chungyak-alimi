from __future__ import annotations

import json
import logging
import sys
from datetime import date

import httpx
from sqlalchemy import exists, select, update

from .analysis import PdfExtractError, analyze_pdf_bytes
from .collectors.applyhome import fetch_apt_house_types, fetch_apt_notices
from .collectors.gh import _ssl_context, fetch_gh_detail_files, fetch_gh_notices
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
    get_notice_analysis,
    init_db,
    mark_notified,
    pending_notifications,
    upsert_house_types,
    upsert_notice_analysis,
    upsert_notices,
)
from .filters import load_filter_config
from .notify import notify_new_matches

logger = logging.getLogger(__name__)

_MAX_ANALYZE_PER_RUN = 20  # 배치당 분석 상한(D4) — 라즈베리파이 CPU 보호
_MAX_PDF_BYTES = 30_000_000  # 다운로드 응답 크기 상한(D5)


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


def enrich_gh_detail() -> int:
    """GH 공고 상세의 PDF 첨부 URL 목록을 raw['_gh_detail']에 채운다. 처리 건수 반환(D1)."""
    added = 0
    with SessionLocal() as session:
        q = select(Notice).where(Notice.source == "gh")
        for n in session.scalars(q).all():
            r = n.raw or {}
            if "_gh_detail" in r:
                continue
            pbanc_no = r.get("_gh_pbanc_no")
            if not pbanc_no:
                continue
            try:
                files = fetch_gh_detail_files(pbanc_no, r.get("biz_ty_cd") or "")
                session.execute(
                    update(Notice)
                    .where(Notice.pblanc_no == n.pblanc_no)
                    .values(raw={**r, "_gh_detail": {"files": files}})
                )
                added += 1
            except Exception:
                logger.exception("GH 상세 보강 실패(pblanc_no=%s) — 건너뜀", n.pblanc_no)
        session.commit()
    return added


def _pdf_target_files(notice: Notice) -> list[dict]:
    """LH/GH 공고의 raw 에서 분석 대상 PDF 파일 목록을 뽑는다(D2). 없으면 빈 리스트."""
    r = notice.raw or {}
    if notice.source == "lh":
        return (r.get("_lh_detail") or {}).get("files") or []
    if notice.source == "gh":
        return (r.get("_gh_detail") or {}).get("files") or []
    return []


def _needs_pdf_analysis(existing, files: list[dict]) -> bool:
    """멱등 스킵 판정(D3) — URL 집합이 같고 실패 항목이 없으면 재분석하지 않는다(D6)."""
    if existing is None:
        return True
    if {f["url"] for f in existing.files} != {f["url"] for f in files}:
        return True
    return any(not f.get("ok") for f in existing.files)


def _analyze_pdf_file(file: dict, client: httpx.Client) -> dict:
    """파일 1건 다운로드+분석. 실패해도 예외를 던지지 않고 ok=False 항목으로 기록한다(D6)."""
    name, url = file["name"], file["url"]
    try:
        resp = client.get(url)
        resp.raise_for_status()
        content = resp.content
        if len(content) > _MAX_PDF_BYTES:
            raise PdfExtractError("파일이 너무 큽니다(too large)")
        analysis = analyze_pdf_bytes(content)
    except (httpx.HTTPError, PdfExtractError) as e:
        return {
            "name": name, "url": url, "ok": False, "error": str(e),
            "page_count": 0, "text_chars": 0, "sections": [],
        }
    return {"name": name, "url": url, "ok": True, "error": None, **analysis.model_dump()}


def enrich_pdf_summaries(*, client: httpx.Client | None = None) -> int:
    """LH/GH 공고의 PDF 를 내려받아 규칙 기반 분석 후 저장한다(D2~D6).

    PDF 원본은 지역 변수로만 존재하며 디스크에 쓰지 않는다(원본 폐기 결정).
    분석을 저장한 공고 수를 반환한다.
    """
    own_default = client is None
    default_client = client or httpx.Client(timeout=30.0, follow_redirects=True)
    gh_client: httpx.Client | None = None  # GH 후보가 있을 때만 생성(apply.gh.or.kr 인증서 이슈, D5)
    analyzed = 0
    try:
        with SessionLocal() as session:
            q = select(Notice).where(Notice.source.in_(("lh", "gh")))
            candidates = []
            for n in session.scalars(q).all():
                files = _pdf_target_files(n)
                if not files:
                    continue
                if _needs_pdf_analysis(get_notice_analysis(session, n.pblanc_no), files):
                    candidates.append((n, files))

            candidates.sort(key=lambda item: item[0].rcrit_pblanc_de or date.min, reverse=True)
            carried = len(candidates) - _MAX_ANALYZE_PER_RUN
            if carried > 0:
                logger.info(
                    "PDF 요약 분석: 이번 배치 %d건 분석, %d건 다음 배치로 이월",
                    _MAX_ANALYZE_PER_RUN, carried,
                )
            batch = candidates[:_MAX_ANALYZE_PER_RUN]

            for n, files in batch:
                try:
                    dl_client = default_client
                    if client is None and n.source == "gh":
                        # GH 파일 서버(apply.gh.or.kr)는 별도 SSL 컨텍스트가 필요하다 —
                        # src.collectors.gh._ssl_context 재사용(같은 코드베이스 내부, D5).
                        if gh_client is None:
                            gh_client = httpx.Client(
                                timeout=30.0, follow_redirects=True, verify=_ssl_context()
                            )
                        dl_client = gh_client
                    results = [_analyze_pdf_file(f, dl_client) for f in files]
                    upsert_notice_analysis(n.pblanc_no, n.source, results, session=session)
                    analyzed += 1
                except Exception:
                    logger.exception("공고 PDF 요약 분석 실패(pblanc_no=%s) — 건너뜀", n.pblanc_no)
            session.commit()
    finally:
        if own_default:
            default_client.close()
        if gh_client is not None:
            gh_client.close()
    return analyzed


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
    gh_detailed = _safe(enrich_gh_detail, "GH 상세 보강", 0)
    pdf_summaries = _safe(enrich_pdf_summaries, "공고 PDF 요약 분석", 0)
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
        "gh_detailed": gh_detailed,
        "pdf_summaries": pdf_summaries,
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
