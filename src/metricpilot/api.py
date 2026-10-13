"""Local analytics API: bounded uploads, two concurrent runs, and inspectable evidence."""

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from metricpilot.config import Settings
from metricpilot.data import MAX_CSV_BYTES, MAX_QUERY_ROWS, MAX_ROWS, DataError, Store
from metricpilot.demo import EXAMPLES, seed_demo
from metricpilot.engine import Engine, markdown_report
from metricpilot.schemas import CreateRun, ImportDataset

WEB = Path(__file__).parent / "web"
MAX_BODY_BYTES = 2_200_000


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        chunks, length = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            length += len(chunk)
            if length > MAX_BODY_BYTES:
                response = JSONResponse({"detail": "Request body is too large."}, 413)
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        pending = True

        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, provider=None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.data_dir / "metricpilot.sqlite3")
    seed_demo(store)
    engine = Engine(settings, store, provider)
    active: set[str] = set()

    @asynccontextmanager
    async def lifespan(_app):
        store.recover()
        yield

    app = FastAPI(title="MetricPilot", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.engine, app.state.active = store, engine, active
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                supplied, expected = urlsplit(origin), urlsplit(str(request.base_url))
                valid = (
                    (supplied.scheme, supplied.netloc) == (expected.scheme, expected.netloc)
                    and not supplied.path
                    and not supplied.query
                    and not supplied.fragment
                )
            except ValueError:
                valid = False
            if not valid:
                return JSONResponse({"detail": "Cross-origin writes are disabled."}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(_request, _exc):
        return JSONResponse({"detail": "Not found."}, 404)

    @app.exception_handler(DataError)
    async def bad_data(_request, exc):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/config")
    def config():
        return {
            "mode": settings.mode,
            "model": settings.model if settings.mode == "live" else "deterministic-fixtures",
            "limits": {
                "max_csv_bytes": MAX_CSV_BYTES,
                "max_rows": MAX_ROWS,
                "max_query_rows": MAX_QUERY_ROWS,
                "max_steps": settings.max_steps,
            },
        }

    @app.get("/api/datasets")
    def datasets():
        return store.datasets()

    @app.post("/api/datasets", status_code=201)
    def import_dataset(payload: ImportDataset):
        return store.import_csv(payload.name, payload.csv_text)

    @app.get("/api/examples")
    def examples():
        return EXAMPLES

    async def execute(identity: str):
        try:
            await engine.execute(identity)
        finally:
            active.discard(identity)

    @app.post("/api/runs", status_code=202)
    async def create_run(payload: CreateRun, background: BackgroundTasks):
        if len(active) >= 2:
            raise HTTPException(429, "At most two analyses can run at once.")
        run = store.create_run(payload.dataset_id, payload.question, settings.mode)
        active.add(run["id"])
        background.add_task(execute, run["id"])
        return run

    @app.get("/api/runs")
    def runs():
        return store.runs()

    @app.get("/api/runs/{identity}")
    def run(identity: str):
        return store.run(identity)

    @app.get("/api/runs/{identity}/queries")
    def queries(identity: str):
        store.run(identity)
        return store.queries(identity)

    @app.get("/api/runs/{identity}/events")
    def events(identity: str):
        store.run(identity)
        return store.events(identity)

    @app.get("/api/runs/{identity}/report")
    def report(identity: str):
        document = store.run(identity)
        if document["status"] != "completed" or not document.get("report"):
            raise HTTPException(409, "The report is not ready.")
        return Response(
            markdown_report(document, store.queries(identity)),
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="metricpilot-report.md"'},
        )

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
