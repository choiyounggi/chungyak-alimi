from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from ..config import settings
from ..models import ApplyhomeHouseType, ApplyhomeNotice

logger = logging.getLogger(__name__)

# 청약홈 APT 분양정보 상세조회 / 주택형별 상세조회
DETAIL_PATH = "/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
MDL_PATH = "/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancMdl"


def _fetch_all(
    path: str,
    model,
    *,
    per_page: int,
    max_pages: int,
    client: httpx.Client | None,
) -> list:
    """odcloud 페이징 API 를 순회하며 model 로 파싱한다.

    빈 페이지 또는 per_page 미만 응답이 오면 종료한다.
    client 를 주입하면(테스트용) 그 클라이언트를 사용하고 닫지 않는다.
    """
    own_client = client is None
    client = client or httpx.Client(base_url=settings.odcloud_base_url, timeout=30.0)
    rows_out: list = []
    try:
        for page in range(1, max_pages + 1):
            resp = client.get(
                path,
                params={
                    "page": page,
                    "perPage": per_page,
                    "serviceKey": settings.odcloud_api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json().get("data", []) or []
            for row in data:
                try:
                    rows_out.append(model.model_validate(row))
                except ValidationError as e:
                    logger.warning("%s 파싱 실패 스킵(page=%s): %s", path, page, e)
            if len(data) < per_page:
                break
    finally:
        if own_client:
            client.close()
    return rows_out


def fetch_apt_notices(
    *,
    per_page: int = 100,
    max_pages: int = 20,
    client: httpx.Client | None = None,
) -> list[ApplyhomeNotice]:
    """청약홈 APT 분양정보(공고)를 수집한다."""
    return _fetch_all(
        DETAIL_PATH, ApplyhomeNotice, per_page=per_page, max_pages=max_pages, client=client
    )


def fetch_apt_house_types(
    *,
    per_page: int = 100,
    max_pages: int = 50,
    client: httpx.Client | None = None,
) -> list[ApplyhomeHouseType]:
    """청약홈 APT 주택형별 상세(면적·분양가)를 수집한다."""
    return _fetch_all(
        MDL_PATH, ApplyhomeHouseType, per_page=per_page, max_pages=max_pages, client=client
    )
