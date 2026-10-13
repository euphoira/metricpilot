import asyncio
import copy

import pytest
from pydantic import ValidationError

from metricpilot.config import Settings
from metricpilot.data import Store, execute_select
from metricpilot.demo import CASES, SEED_ID, seed_demo
from metricpilot.engine import Engine, WorkflowError, markdown_report, verify_report
from metricpilot.provider import Completion, ToolCall
from metricpilot.schemas import Report


@pytest.fixture
def workspace(tmp_path):
    store = Store(tmp_path / "engine.sqlite3")
    dataset = seed_demo(store)
    return Settings(data_dir=tmp_path), store, dataset


def report_for(query, **changes):
    return {
        "title": "收入概览",
        "summary": "查询给出了当前数据的收入汇总。",
        "claims": [
            {
                "label": "总收入",
                "query_id": query["id"],
                "row": 0,
                "column": query["columns"][0],
                "value": query["rows"][0][0],
                "unit": "USD",
            }
        ],
        "chart": None,
        "limitations": [],
        "verified": False,
        **changes,
    }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_demo_executes_real_sql_and_validates_exact_cell(workspace, case):
    settings, store, dataset = workspace
    run = store.create_run(dataset["id"], case["question"], "demo")
    result = await Engine(settings, store).execute(run["id"])
    assert result["status"] == "completed", result["error"]
    assert result["report"]["verified"]
    claim = result["report"]["claims"][0]
    assert claim["value"] == case["expected"]
    queries = store.queries(run["id"])
    assert len(queries) == 1
    assert claim["query_id"] == queries[0]["id"]
    assert (
        claim["value"]
        == queries[0]["rows"][claim["row"]][queries[0]["columns"].index(claim["column"])]
    )
    assert result["metrics"]["model_calls"] == 0
    assert result["metrics"]["tool_calls"] == 2
    assert Store(store.path).run(run["id"]) == result
    assert "SELECT" in markdown_report(result, queries)
    assert await Engine(settings, store).execute(run["id"]) == result


async def test_unknown_questions_and_imports_abstain_in_demo(workspace):
    settings, store, dataset = workspace
    imported = store.import_csv("My imports", "value\n1\n")
    for identity, question in [
        (dataset["id"], "Predict future revenue"),
        (imported["id"], CASES[0]["question"]),
    ]:
        run = store.create_run(identity, question, "demo")
        result = await Engine(settings, store).execute(run["id"])
        assert result["status"] == "completed"
        assert not result["report"]["verified"]
        assert result["report"]["claims"] == []
        assert store.queries(run["id"]) == []


@pytest.fixture
def queried(workspace):
    settings, store, dataset = workspace
    run = store.create_run(dataset["id"], CASES[0]["question"], "live")
    query = execute_select(
        store, run, f"SELECT SUM(revenue) AS revenue FROM {dataset['table_name']}"
    )
    return settings, store, dataset, run, query


@pytest.mark.parametrize(
    "change",
    [
        {"value": 9999},
        {"value": "8000"},
        {"row": 1},
        {"query_id": "fabricated"},
        {"column": "missing"},
        {"label": "Revenue 9999"},
        {"unit": "USD 9999"},
    ],
)
def test_fabricated_or_misleading_claims_cannot_pass(queried, change):
    _, _, _, run, query = queried
    draft = report_for(query)
    draft["claims"][0].update(change)
    with pytest.raises(WorkflowError):
        verify_report(Report.model_validate(draft), run, [query])


def test_prose_numbers_bool_values_and_cross_run_query_ids_are_rejected(queried):
    _, _, _, run, query = queried
    with pytest.raises(WorkflowError):
        verify_report(
            Report.model_validate(report_for(query, summary="Revenue is 9999")), run, [query]
        )
    draft = report_for(query)
    draft["claims"][0]["value"] = True
    with pytest.raises(ValidationError):
        Report.model_validate(draft)
    other_run = {**run, "id": "another-run"}
    with pytest.raises(WorkflowError):
        verify_report(Report.model_validate(report_for(query)), other_run, [query])


def test_chart_requires_own_query_and_numeric_nonnegative_column(queried):
    _, store, dataset, run, query = queried
    chart_query = execute_select(store, run, f"SELECT region,revenue FROM {dataset['table_name']}")
    chart = {
        "query_id": chart_query["id"],
        "label_column": "region",
        "value_column": "revenue",
        "title": "地区收入",
    }
    result = verify_report(
        Report.model_validate(report_for(query, chart=chart)), run, [query, chart_query]
    )
    assert result.verified
    for change in (
        {"query_id": "foreign"},
        {"value_column": "region"},
        {"label_column": "missing"},
    ):
        with pytest.raises(WorkflowError):
            verify_report(
                Report.model_validate(report_for(query, chart={**chart, **change})),
                run,
                [query, chart_query],
            )


def test_truncation_and_limitations_remain_visible_and_schema_valid(queried):
    _, _, _, run, query = queried
    query["truncated"] = True
    report = Report.model_validate(report_for(query, limitations=["已有局限"] * 8))
    result = verify_report(report, run, [query])
    assert len(result.limitations) == 8
    assert any("截断" in limitation for limitation in result.limitations)
    assert any("不证明 SQL" in limitation for limitation in result.limitations)
    Report.model_validate(result.model_dump())


class ScriptedProvider:
    def __init__(self, responses):
        self.responses, self.messages = iter(responses), []

    async def complete(self, messages, tools=None):
        self.messages.append(copy.deepcopy(messages))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response() if callable(response) else response


async def test_live_flow_uses_function_calls_and_persisted_query_ids(workspace):
    settings, store, dataset = workspace
    settings = Settings(
        mode="live", api_key="test-only", model="test-model", data_dir=settings.data_dir
    )
    run = store.create_run(dataset["id"], "Calculate total revenue", "live")

    def submit():
        query = store.queries(run["id"])[0]
        return Completion(tool_calls=[ToolCall("submit", "submit_report", report_for(query))])

    provider = ScriptedProvider(
        [
            Completion(
                tool_calls=[
                    ToolCall("schema", "inspect_schema", {}),
                    ToolCall("profile", "profile_dataset", {}),
                ]
            ),
            Completion(
                tool_calls=[
                    ToolCall(
                        "query",
                        "query_sql",
                        {"sql": f"SELECT SUM(revenue) AS revenue FROM {dataset['table_name']}"},
                    )
                ]
            ),
            submit,
        ]
    )
    result = await Engine(settings, store, provider).execute(run["id"])
    assert result["status"] == "completed", result["error"]
    assert result["report"]["claims"][0]["value"] == 8000
    assert result["metrics"]["model_calls"] == 3
    assert result["metrics"]["tool_calls"] == 3
    assert [message["tool_call_id"] for message in provider.messages[1][-2:]] == [
        "schema",
        "profile",
    ]
    assert provider.messages[2][-1]["tool_call_id"] == "query"


async def test_live_budget_and_interruption_are_persisted(workspace):
    settings, store, dataset = workspace
    settings = Settings(
        mode="live",
        api_key="test-only",
        model="test-model",
        max_steps=1,
        data_dir=settings.data_dir,
    )
    run = store.create_run(dataset["id"], "Inspect dataset", "live")
    provider = ScriptedProvider([Completion(tool_calls=[ToolCall("schema", "inspect_schema", {})])])
    result = await Engine(settings, store, provider).execute(run["id"])
    assert result["status"] == "failed" and result["report"] is None
    assert result["metrics"]["model_calls"] == 1
    interrupted = store.create_run(dataset["id"], "Inspect dataset", "live")
    with pytest.raises(asyncio.CancelledError):
        await Engine(settings, store, ScriptedProvider([asyncio.CancelledError()])).execute(
            interrupted["id"]
        )
    assert Store(store.path).run(interrupted["id"])["status"] == "interrupted"
    assert store.run(interrupted["id"])["metrics"]["model_calls"] == 1


def test_restart_marks_all_active_runs_interrupted(workspace):
    _, store, _ = workspace
    queued = store.create_run(SEED_ID, "Queued analysis", "demo")
    running = store.create_run(SEED_ID, "Running analysis", "demo")
    assert store.claim(running["id"])
    assert store.claim(running["id"]) is None
    restarted = Store(store.path)
    restarted.recover()
    assert restarted.run(queued["id"])["status"] == "interrupted"
    assert restarted.run(running["id"])["status"] == "interrupted"
