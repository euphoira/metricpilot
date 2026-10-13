"""Installable CLI; demo and evaluation always remain offline."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from metricpilot.config import Settings
from metricpilot.data import Store
from metricpilot.demo import EXAMPLES, seed_demo
from metricpilot.engine import Engine, markdown_report
from metricpilot.evaluation import evaluate


def main():
    # Windows redirected streams may default to a code page that cannot print Chinese.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="metricpilot", description="Analytics with query evidence"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local analytics studio")
    serve.add_argument("--port", type=int, default=8011)
    demo = commands.add_parser(
        "demo", help="Run a deterministic offline question against sample data"
    )
    demo.add_argument("--question", default=EXAMPLES[0]["question"])
    demo.add_argument("--output", type=Path, default=Path("artifacts/demo-report.md"))
    evaluation = commands.add_parser("eval", help="Run offline synthetic regression cases")
    evaluation.add_argument("--output", type=Path, default=Path("artifacts/eval-results.json"))
    args = parser.parse_args()
    try:
        if args.command == "serve":
            import uvicorn

            from metricpilot.api import create_app

            uvicorn.run(
                create_app(Settings.from_env()), host="127.0.0.1", port=args.port, workers=1
            )
        elif args.command == "eval":
            result = asyncio.run(evaluate(args.output))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(0 if result["passed"] == result["total"] else 1)
        else:
            settings = Settings(data_dir=Path(os.getenv("METRICPILOT_DATA_DIR", ".metricpilot")))
            store = Store(settings.data_dir / "metricpilot.sqlite3")
            dataset = seed_demo(store)
            run = store.create_run(dataset["id"], args.question, "demo")
            result = asyncio.run(Engine(settings, store).execute(run["id"]))
            if result["status"] != "completed":
                parser.exit(1, "metricpilot: analysis did not complete\n")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(markdown_report(result, store.queries(run["id"])), "utf-8")
            print(
                json.dumps(
                    {
                        "mode": "demo",
                        "status": result["status"],
                        "report": str(args.output),
                        "metrics": result["metrics"],
                    },
                    indent=2,
                )
            )
    except (ValueError, OSError) as exc:
        parser.exit(1, f"metricpilot: {exc}\n")


if __name__ == "__main__":
    main()
