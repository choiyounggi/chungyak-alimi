from __future__ import annotations

import httpx

from ..config import settings
from ..models import ApplyhomeNotice

# 청약홈 APT 분양정보 상세조회
DETAIL_PATH = "/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"


def fetch_apt_notices(
    *,
    per_page: int = 100,
    max_pages: int = 20,
    client: httpx.Client | None = None,
) -> list[ApplyhomeNotice]:
    """청약홈 APT 분양정보를 페이징 순회하며 수집한다.

    빈 페이지 또는 per_page 미만 응답이 오면 종료한다.
    client 를 주입하면(테스트용) 그 클라이언트를 사용하고 닫지 않는다.
    """
    own_client = client is None
    client = client or httpx.Client(base_url=settings.odcloud_base_url, timeout=30.0)
    notices: list[ApplyhomeNotice] = []
    try:
        for page in range(1, max_pages + 1):
            resp = client.get(
                DETAIL_PATH,
                params={
                    "page": page,
                    "perPage": per_page,
                    "serviceKey": settings.odcloud_api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json().get("data", []) or []
            notices.extend(ApplyhomeNotice.model_validate(row) for row in data)
            if len(data) < per_page:
                break
    finally:
        if own_client:
            client.close()
    return notices
