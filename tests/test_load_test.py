import asyncio
from types import SimpleNamespace

import httpx
from scripts import load_test


class FakeResponse:
    status_code = 200
    content = b"ok"


async def fake_request(self, *args, **kwargs):
    return FakeResponse()


def test_report_distinguishes_successful_and_attempted_throughput(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    args = SimpleNamespace(
        base_url="http://example.test",
        header=[],
        issue_api_key=False,
        admin_token_env="KAYKAY_ADMIN_TOKEN",
        timeout=2.0,
        payload='{"sample": true}',
        concurrency=2,
        requests=4,
        method="POST",
        path="/health",
    )

    report = asyncio.run(load_test.run(args))

    assert report["success_count"] == 4
    assert report["error_count"] == 0
    assert report["throughput_requests_per_second"] == report["attempted_requests_per_second"]
