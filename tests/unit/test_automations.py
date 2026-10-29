from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from lara.dispatch.models import next_occurrence
from lara_api.app import create_app


def test_rrule_uses_named_timezone_across_dst() -> None:
    result = next_occurrence(
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        timezone_name="America/New_York",
        dtstart="2026-03-01T14:00:00+00:00",
        after="2026-03-08T13:30:00+00:00",
    )

    assert result == "2026-03-09T13:00:00+00:00"


@pytest.mark.asyncio
async def test_automation_api_crud_and_manual_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(workspace_root=str(workspace))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/automations",
            json={
                "name": "daily",
                "prompt": "Inspect",
                "workspace_root": str(workspace),
                "destination": "standalone",
                "timezone": "UTC",
                "rrule": "FREQ=DAILY;BYHOUR=9",
            },
        )
        assert response.status_code == 201
        automation_id = response.json()["automation_id"]
        assert (await client.post(f"/automations/{automation_id}/pause")).json()[
            "status"
        ] == "paused"
        assert (await client.post(f"/automations/{automation_id}/resume")).json()[
            "status"
        ] == "active"
        run = (await client.post(f"/automations/{automation_id}/run")).json()
        assert run["status"] == "queued"
        runs = (await client.get(f"/automations/{automation_id}/runs")).json()
        assert runs["runs"][0]["run_id"] == run["run_id"]
        missing = await client.post(
            f"/automations/{automation_id}/runs/arun-missing/cancel"
        )
        assert missing.status_code == 404
        # The scheduler is not started here, so the queued run cannot be
        # cancelled in-process.
        not_running = await client.post(
            f"/automations/{automation_id}/runs/{run['run_id']}/cancel"
        )
        assert not_running.status_code == 409
