"""API contracts and boundaries exercised through HTTP, without a real model."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from metricpilot.api import MAX_BODY_BYTES, create_app
from metricpilot.config import Settings
from metricpilot.demo import EXAMPLES, SEED_ID
from metricpilot.evaluation import evaluate


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as instance:
        yield instance


def test_dashboard_assets_and_config(client):
    assert client.get("/").status_code == 200
    assert "MetricPilot" in client.get("/").text
    for path in ["/static/app.js", "/static/style.css", "/health", "/openapi.json"]:
        assert client.get(path).status_code == 200
    response = client.get("/api/config")
    assert response.json()["mode"] == "demo"
    assert "api_key" not in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_complete_run_and_report_export(client):
    examples = client.get("/api/examples").json()
    response = client.post(
        "/api/runs",
        json={
            "dataset_id": SEED_ID,
            "question": examples[0]["question"],
        },
    )
    assert response.status_code == 202
    identity = response.json()["id"]
    run = client.get(f"/api/runs/{identity}").json()
    assert run["status"] == "completed" and run["report"]["verified"]
    assert run["report"]["claims"][0]["value"] == 8000
    query = client.get(f"/api/runs/{identity}/queries").json()[0]
    assert query["id"] == run["report"]["claims"][0]["query_id"]
    assert query["rows"] == [[8000]]
    assert client.get(f"/api/runs/{identity}/events").json()[-1]["type"] == "completed"
    report = client.get(f"/api/runs/{identity}/report")
    assert report.status_code == 200 and "8000" in report.text
    assert "attachment" in report.headers["content-disposition"]
    assert client.get("/api/runs").json()[0]["id"] == identity


def test_import_and_demo_abstention(client):
    dataset = client.post(
        "/api/datasets",
        json={
            "name": "My data",
            "csv_text": "region,revenue\nEast,7\nWest,3\n",
        },
    )
    assert dataset.status_code == 201
    metadata = dataset.json()
    assert metadata["row_count"] == 2
    assert metadata["table_name"].startswith("d_")
    run = client.post(
        "/api/runs",
        json={
            "dataset_id": metadata["id"],
            "question": EXAMPLES[0]["question"],
        },
    ).json()
    report = client.get(f"/api/runs/{run['id']}").json()["report"]
    assert report["claims"] == [] and report["verified"] is False
    assert report["limitations"]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "bad", "csv_text": "duplicate,duplicate\n1,2"},
        {"name": "bad", "csv_text": "x,y\n1"},
        {"name": "bad", "csv_text": "x;drop table,y\n1,2"},
        {"name": "bad", "csv_text": "x\n1", "table_name": "secrets"},
    ],
)
def test_bad_import_rejected(client, payload):
    assert client.post("/api/datasets", json=payload).status_code == 422


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.example",
        "null",
        "http://testserver:9999",
        "http://testserver/path",
        "http://testserver?unexpected=1",
        "http://[",
    ],
)
def test_foreign_or_malformed_origin_rejected(client, origin):
    response = client.post(
        "/api/runs",
        headers={"Origin": origin},
        json={
            "dataset_id": SEED_ID,
            "question": EXAMPLES[0]["question"],
        },
    )
    assert response.status_code == 403
    assert client.get("/api/runs").json() == []


def test_same_origin_and_host_checks(client):
    assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 400
    assert (
        client.post(
            "/api/runs",
            headers={"Origin": "http://testserver"},
            json={
                "dataset_id": SEED_ID,
                "question": EXAMPLES[0]["question"],
            },
        ).status_code
        == 202
    )


def test_request_size_budget(client):
    response = client.post("/api/datasets", content=b"x" * (MAX_BODY_BYTES + 1))
    assert response.status_code == 413


@pytest.mark.parametrize("suffix", ["", "/queries", "/events", "/report"])
def test_unknown_run_is_404(client, suffix):
    assert client.get("/api/runs/missing" + suffix).status_code == 404


def test_unfinished_report_is_409(client):
    run = client.app.state.store.create_run(SEED_ID, "unstarted question", "demo")
    assert client.get(f"/api/runs/{run['id']}/report").status_code == 409


@pytest.mark.asyncio
async def test_two_active_runs_bound_and_release(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path))
    release, both_started = asyncio.Event(), asyncio.Event()
    started = 0

    async def blocked(_identity):
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()

    monkeypatch.setattr(app.state.engine, "execute", blocked)
    transport = httpx.ASGITransport(app=app)
    payload = {"dataset_id": SEED_ID, "question": "A bounded question"}
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as browser:
        tasks = [asyncio.create_task(browser.post("/api/runs", json=payload)) for _ in range(2)]
        try:
            await asyncio.wait_for(both_started.wait(), 3)
            assert (await browser.post("/api/runs", json=payload)).status_code == 429
        finally:
            release.set()
            responses = await asyncio.gather(*tasks)
        assert all(response.status_code == 202 for response in responses)
        assert not app.state.active


@pytest.mark.asyncio
async def test_evaluation_fixture_truth_and_abstention(tmp_path):
    output = tmp_path / "eval.json"
    result = await evaluate(output)
    assert result["passed"] == result["total"] == 8
    assert output.exists()
