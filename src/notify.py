from __future__ import annotations

from urllib.parse import quote

import httpx

from .config import settings
from .db import SessionLocal, house_types_of, mark_notified, pending_notifications
from .scoring import judge_notice, load_profile


def format_notice(notice, house_types, profile=None) -> str:
    """공고 1건을 텔레그램 메시지(HTML)로 포맷."""
    prices = [h.lttot_top_amount for h in house_types if h.lttot_top_amount]
    if prices:
        lo, hi = min(prices), max(prices)
        price_str = f"{lo:,}만원" if lo == hi else f"{lo:,}~{hi:,}만원"
    else:
        price_str = "-"

    seg = " ".join(filter(None, [notice.house_secd_nm, notice.house_dtl_secd_nm]))
    lines = [
        f"🏠 <b>{notice.house_nm}</b>",
        f"📍 {notice.area_nm or '-'} | {seg or '-'}",
        f"🗓 접수 {notice.rcept_bgnde} ~ {notice.rcept_endde}",
        f"💰 {price_str} | {notice.tot_suply_hshldco or '-'}세대",
    ]
    if notice.pblanc_url:
        lines.append(f"🔗 {notice.pblanc_url}")
    if settings.public_base_url:
        base = settings.public_base_url.rstrip("/")
        lines.append(f"🔎 {base}/notice/{quote(notice.pblanc_no, safe='')}")
    if profile is not None:
        judged = judge_notice(notice, house_types, profile)
        if judged["supported"]:
            lines.append(f"🎯 {judged['summary']}")
    return "\n".join(lines)


def send_telegram(
    text: str,
    *,
    token: str | None = None,
    chat_id: str | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """텔레그램 sendMessage. 토큰/chat_id 는 기본 settings 사용."""
    token = token or settings.tg_bot_token
    chat_id = chat_id or settings.tg_chat_id
    if not token or not chat_id:
        raise RuntimeError("TG_BOT_TOKEN / TG_CHAT_ID 가 설정되지 않았습니다(.env)")

    own = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        resp = client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        # raise_for_status 예외 메시지엔 URL(=봇 토큰)이 포함되므로 그대로 전파하지 않는다.
        if resp.status_code >= 400:
            raise RuntimeError(f"텔레그램 발송 실패: HTTP {resp.status_code}")
        return resp.json()
    finally:
        if own:
            client.close()


def notify_new_matches(*, client: httpx.Client | None = None, channel: str = "telegram") -> int:
    """매칭됐지만 미발송인 공고를 텔레그램으로 보내고 이력 기록. 발송 건수 반환."""
    sent = 0
    profile = load_profile()
    with SessionLocal() as session:
        pending = pending_notifications(channel=channel, session=session)
        for notice in pending:
            hts = house_types_of(notice.pblanc_no, session=session)
            send_telegram(format_notice(notice, hts, profile=profile), client=client)
            mark_notified(notice.pblanc_no, channel=channel, session=session)
            sent += 1
    return sent
