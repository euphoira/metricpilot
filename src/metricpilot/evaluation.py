"""Deterministic fixture regression; no live-model quality claims."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from metricpilot.config import Settings
from metricpilot.data import Store
from metricpilot.demo import CASES, EXAMPLES, seed_demo
from metricpilot.engine import Engine


async def evaluate(output: Path | None = None) -> dict:
    cases = []
    with TemporaryDirectory(prefix="metricpilot-eval-") as directory:
        settings = Settings(data_dir=Path(directory))
        store = Store(settings.data_dir / "eval.sqlite3")
        dataset = seed_demo(store)
        engine = Engine(settings, store)
        expected_values = {case["id"]: case["expected"] for case in CASES}
        for example in EXAMPLES:
            run = store.create_run(dataset["id"], example["question"], "demo")
            result = await engine.execute(run["id"])
            report = result.get("report") or {}
            queries = {item["id"]: item for item in store.queries(run["id"])}
            claims = report.get("claims", [])
            valid = result["status"] == "completed" and report.get("verified") and bool(claims)
            valid = valid and claims[0]["value"] == expected_values[example["id"]]
            for claim in claims:
                query = queries.get(claim["query_id"])
                try:
                    value = query["rows"][claim["row"]][query["columns"].index(claim["column"])]
                    valid = valid and claim["value"] == value
                except (KeyError, IndexError, ValueError, TypeError):
                    valid = False
            cases.append(
                {
                    "case": example["id"],
                    "passed": bool(valid),
                    "kind": "offline_workflow_and_cell_provenance",
                }
            )
        run = store.create_run(dataset["id"], "Predict next year's weather in Tokyo", "demo")
        result = await engine.execute(run["id"])
        report = result.get("report") or {}
        cases.append(
            {
                "case": "unsupported-question",
                "kind": "abstention",
                "passed": bool(
                    result["status"] == "completed"
                    and not report.get("claims")
                    and report.get("limitations")
                ),
            }
        )
    result = {
        "mode": "demo",
        "scope": "Synthetic deterministic regression; not live-model accuracy or SQL correctness",
        "passed": sum(item["passed"] for item in cases),
        "total": len(cases),
        "cases": cases,
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return result
