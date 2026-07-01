from __future__ import annotations

import httpx

from src.collectors import vworld
from src.collectors.vworld import fetch_parcel_polygon

GEO = {"response": {"status": "OK", "result": {"point": {"x": "127.05", "y": "37.49"}}}}
DATA = {"response": {"status": "OK", "result": {"featureCollection": {"features": [
    {"geometry": {"type": "Polygon", "coordinates": [[[127.0, 37.4], [127.1, 37.4], [127.1, 37.5], [127.0, 37.4]]]}}
]}}}}


def _client():
    def handler(request: httpx.Request) -> httpx.Response:
        if "req/address" in str(request.url):
            return httpx.Response(200, json=GEO)
        return httpx.Response(200, json=DATA)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_parcel_polygon(monkeypatch):
    monkeypatch.setattr(vworld.settings, "vworld_key", "K")
    with _client() as c:
        poly = fetch_parcel_polygon("서울특별시 강남구 도곡동 527", client=c)
    assert poly is not None
    assert poly[0] == [127.0, 37.4]  # [경도,위도]


def test_no_key_returns_none(monkeypatch):
    monkeypatch.setattr(vworld.settings, "vworld_key", "")
    assert fetch_parcel_polygon("서울 어딘가") is None


def test_empty_geocode_returns_none(monkeypatch):
    monkeypatch.setattr(vworld.settings, "vworld_key", "K")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": {"status": "NOT_FOUND"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert fetch_parcel_polygon("없는주소", client=c) is None
