from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://api.vworld.kr/req/address"
DATA_URL = "https://api.vworld.kr/req/data"


def _geocode(addr: str, client: httpx.Client) -> tuple[float, float] | None:
    """지번주소 → (경도, 위도). V-World geocoder."""
    resp = client.get(
        GEOCODE_URL,
        params={
            "service": "address",
            "request": "getcoord",
            "address": addr,
            "type": "PARCEL",
            "key": settings.vworld_key,
            "format": "json",
        },
    )
    resp.raise_for_status()
    body = resp.json().get("response", {})
    if body.get("status") != "OK":
        return None
    p = body["result"]["point"]
    return float(p["x"]), float(p["y"])


def _parcel_outline(x: float, y: float, client: httpx.Client) -> list[list[float]] | None:
    """좌표가 속한 필지의 외곽 경계 [[경도,위도], ...]. V-World 연속지적도."""
    resp = client.get(
        DATA_URL,
        params={
            "service": "data",
            "request": "GetFeature",
            "data": "LP_PA_CBND_BUBUN",
            "key": settings.vworld_key,
            "domain": settings.vworld_domain,
            "geomFilter": f"POINT({x} {y})",
            "geometry": "true",
            "format": "json",
            "crs": "EPSG:4326",
            "size": "1",
        },
    )
    resp.raise_for_status()
    body = resp.json().get("response", {})
    if body.get("status") != "OK":
        return None
    feats = body.get("result", {}).get("featureCollection", {}).get("features", [])
    if not feats:
        return None
    geom = feats[0].get("geometry", {})
    coords = geom.get("coordinates")
    if not coords:
        return None
    # Polygon: [ring, ...] / MultiPolygon: [[ring, ...], ...] → 첫 외곽 링
    ring = coords[0][0] if geom.get("type") == "MultiPolygon" else coords[0]
    return [[float(pt[0]), float(pt[1])] for pt in ring]


def fetch_parcel_polygon(addr: str, *, client: httpx.Client | None = None) -> list[list[float]] | None:
    """주소 → 필지 외곽 폴리곤 좌표. 실패 시 None(마커만 표시하게)."""
    if not settings.vworld_key or not addr:
        return None
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        # "외 N개 동 일원" 등 접미사 제거해 지번 정확도를 높인다
        short = " ".join(addr.split()[:4])
        for candidate in (short, addr):
            coord = _geocode(candidate, client)
            if coord:
                outline = _parcel_outline(coord[0], coord[1], client)
                if outline:
                    return outline
        return None
    except httpx.HTTPError as e:
        logger.warning("V-World 폴리곤 조회 실패(%s): %s", addr, e)
        return None
    finally:
        if own:
            client.close()
