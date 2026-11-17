from __future__ import annotations

import httpx
from app.services.supplier_sync import DummyJsonClient


def test_client_follows_limit_skip_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        skip = int(request.url.params["skip"])
        page = [{"id": skip + 1}] if skip == 0 else [{"id": skip + 1}]
        return httpx.Response(200, json={"products": page, "total": 2, "skip": skip, "limit": 1})

    source = DummyJsonClient(
        base_url="https://supplier.example",
        timeout_seconds=1,
        max_retries=0,
        page_size=1,
        client=httpx.Client(
            base_url="https://supplier.example", transport=httpx.MockTransport(handler)
        ),
    )

    assert list(source.iter_products()) == [{"id": 1}, {"id": 2}]
    assert [request.url.params["skip"] for request in requests] == ["0", "1"]
    assert all(request.url.params["limit"] == "1" for request in requests)


def test_client_retries_a_server_error(monkeypatch: object) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            request=request,
            json={"products": [{"id": 1}], "total": 1, "skip": 0, "limit": 1},
        )

    monkeypatch.setattr("app.services.supplier_sync.time.sleep", lambda _: None)  # type: ignore[attr-defined]
    source = DummyJsonClient(
        base_url="https://supplier.example",
        timeout_seconds=1,
        max_retries=1,
        page_size=1,
        client=httpx.Client(
            base_url="https://supplier.example", transport=httpx.MockTransport(handler)
        ),
    )

    assert list(source.iter_products()) == [{"id": 1}]
    assert attempts == 2
