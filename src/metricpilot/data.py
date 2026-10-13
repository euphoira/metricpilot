"""Bounded CSV ingestion and isolated, authorizer-protected SQLite SELECT execution."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class DataError(ValueError):
    pass


MAX_CSV_BYTES = 1_048_576
MAX_ROWS = 10_000
MAX_COLUMNS = 30
MAX_CELL_BYTES = 4096
MAX_QUERY_ROWS = 200
MAX_RESULT_BYTES = 131_072
MAX_OPCODES = 200_000
QUERY_TIMEOUT = 0.75
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")
FUNCTIONS = frozenset(
    {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "total",
        "round",
        "abs",
        "coalesce",
        "ifnull",
        "nullif",
        "length",
        "lower",
        "upper",
        "substr",
        "substring",
        "trim",
        "ltrim",
        "rtrim",
        "date",
        "strftime",
        "julianday",
        "unixepoch",
    }
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def parse_csv(text: str) -> tuple[list[dict], list[list]]:
    if not isinstance(text, str):
        raise DataError("CSV 必须是 UTF-8 文本。")
    try:
        raw = text.encode("utf-8")
    except UnicodeError:
        raise DataError("CSV 包含无效字符。") from None
    if len(raw) > MAX_CSV_BYTES or not raw:
        raise DataError("CSV 必须非空且不超过一 MiB。")
    if "\x00" in text:
        raise DataError("CSV 不允许二进制内容。")
    try:
        reader = csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
        header = next(reader)
        if not 1 <= len(header) <= MAX_COLUMNS:
            raise DataError("CSV 必须包含一至三十列。")
        if any(not IDENTIFIER.fullmatch(name) for name in header):
            raise DataError("列名需以英文字母开头，仅含字母、数字或下划线，最长四十八字符。")
        if len({name.lower() for name in header}) != len(header):
            raise DataError("CSV 列名不可重复（不区分大小写）。")
        rows = []
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise DataError("CSV 超过一万行上限。")
            if len(row) != len(header):
                raise DataError("CSV 各行列数必须与表头一致。")
            lengths = [len(value.encode("utf-8")) for value in row]
            if any(length > MAX_CELL_BYTES for length in lengths) or sum(lengths) > 8192:
                raise DataError("CSV 单元格或单行内容过长。")
            rows.append([value if value != "" else None for value in row])
    except (csv.Error, StopIteration):
        raise DataError("CSV 格式无效。") from None
    if not rows:
        raise DataError("CSV 至少需要一行数据。")
    columns = []
    for index, name in enumerate(header):
        values = [row[index] for row in rows if row[index] is not None]
        kind = "TEXT"
        if values and all(INTEGER.fullmatch(value) for value in values):
            if all(abs(int(value)) <= 2**53 - 1 for value in values):
                kind = "INTEGER"
        if kind == "TEXT" and values and all(NUMBER.fullmatch(value) for value in values):
            numbers = [float(value) for value in values]
            if all(math.isfinite(value) and abs(value) <= 10**15 for value in numbers):
                kind = "REAL"
        if kind != "TEXT":
            convert = int if kind == "INTEGER" else float
            for row in rows:
                if row[index] is not None:
                    row[index] = convert(row[index])
        columns.append({"name": name, "type": kind})
    return columns, rows


class Store:
    """One local server process; metadata and immutable dataset snapshots in SQLite."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS queries (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, document TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS queries_run ON queries(run_id);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    document TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id);
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def import_csv(self, name: str, text: str, *, dataset_id: str | None = None) -> dict:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise DataError("数据集名称长度需为一至八十字符。")
        columns, rows = parse_csv(text)
        identity = dataset_id or uuid4().hex
        if not re.fullmatch(r"[a-z0-9_]{1,40}", identity):
            raise DataError("数据集标识无效。")
        dataset = {
            "id": identity,
            "name": name.strip(),
            "table_name": "d_" + uuid4().hex,
            "columns": columns,
            "row_count": len(rows),
            "created_at": now(),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] >= 20:
                raise DataError("本地工作区最多保存二十个数据集。")
            conn.execute(
                "INSERT INTO datasets VALUES (?, ?, ?)",
                (identity, json.dumps(dataset), json.dumps(rows)),
            )
        return dataset

    def datasets(self) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("SELECT document FROM datasets ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def dataset(self, identity: str) -> tuple[dict, list[list]]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT document,data FROM datasets WHERE id=?", (identity,)
            ).fetchone()
        if row is None:
            raise KeyError(identity)
        return json.loads(row[0]), json.loads(row[1])

    def create_run(self, dataset_id: str, question: str, mode: str) -> dict:
        self.dataset(dataset_id)
        run = {
            "id": uuid4().hex,
            "dataset_id": dataset_id,
            "question": question,
            "mode": mode,
            "status": "queued",
            "created_at": now(),
            "error": None,
            "report": None,
            "metrics": {"model_calls": 0, "tool_calls": 0, "duration_ms": 0},
        }
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] >= 500:
                raise DataError("工作区已达五百次运行上限；请使用新的数据目录。")
            conn.execute("INSERT INTO runs VALUES (?, ?)", (run["id"], json.dumps(run)))
        return run

    def save_run(self, run: dict):
        with self.connection() as conn:
            conn.execute("UPDATE runs SET document=? WHERE id=?", (json.dumps(run), run["id"]))

    def run(self, identity: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT document FROM runs WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return json.loads(row[0])

    def runs(self) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("SELECT document FROM runs ORDER BY rowid DESC LIMIT 30").fetchall()
        return [json.loads(row[0]) for row in rows]

    def claim(self, identity: str) -> dict | None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT document FROM runs WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError(identity)
            run = json.loads(row[0])
            if run["status"] != "queued":
                return None
            run["status"] = "running"
            conn.execute("UPDATE runs SET document=? WHERE id=?", (json.dumps(run), identity))
        return run

    def recover(self):
        with self.connection() as conn:
            rows = conn.execute("SELECT id,document FROM runs").fetchall()
            for identity, document in rows:
                run = json.loads(document)
                if run["status"] in {"queued", "running"}:
                    run["status"], run["error"] = "interrupted", "进程中断，请创建新的分析任务。"
                    conn.execute(
                        "UPDATE runs SET document=? WHERE id=?", (json.dumps(run), identity)
                    )

    def save_query(self, query: dict):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO queries VALUES (?, ?, ?)",
                (query["id"], query["run_id"], json.dumps(query, allow_nan=False)),
            )

    def queries(self, run_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT document FROM queries WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def event(self, run_id: str, kind: str, message: str, data: dict | None = None):
        event = {
            "run_id": run_id,
            "type": kind,
            "message": message,
            "data": data or {},
            "created_at": now(),
        }
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO events (run_id,document) VALUES (?,?)", (run_id, json.dumps(event))
            )

    def events(self, run_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id,document FROM events WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        return [{"id": row[0], **json.loads(row[1])} for row in rows]


def profile(dataset: dict, rows: list[list]) -> dict:
    columns = []
    for index, column in enumerate(dataset["columns"]):
        values = [row[index] for row in rows if row[index] is not None]
        item = {**column, "null_count": len(rows) - len(values), "distinct": len(set(values))}
        if column["type"] != "TEXT" and values:
            item.update(min=min(values), max=max(values))
        else:
            item["sample"] = list(dict.fromkeys(values))[:3]
        columns.append(item)
    return {"table_name": dataset["table_name"], "row_count": len(rows), "columns": columns}


def execute_select(store: Store, run: dict, sql: str) -> dict:
    if not isinstance(sql, str) or len(sql) > 6000 or not re.match(r"\A\s*SELECT\b", sql, re.I):
        raise DataError("仅允许以 SELECT 开头、不超过六千字符的单条查询。")
    dataset, source = store.dataset(run["dataset_id"])
    table = dataset["table_name"]
    conn = sqlite3.connect(":memory:")
    started = time.monotonic()
    try:
        declarations = ",".join(f'"{c["name"]}" {c["type"]}' for c in dataset["columns"])
        conn.execute(f'CREATE TABLE "{table}" ({declarations})')
        slots = ",".join("?" for _ in dataset["columns"])
        conn.executemany(f'INSERT INTO "{table}" VALUES ({slots})', source)
        conn.commit()
        conn.execute("PRAGMA query_only=ON")
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 32_768)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 6000)
        conn.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 40)
        conn.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 30)
        conn.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 4)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, 200)
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        read_dataset = False

        def authorize(action, arg1, arg2, database, trigger):
            nonlocal read_dataset
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if (
                action == sqlite3.SQLITE_READ
                and arg1 == table
                and (database == "main" or database is None and arg2 == "")
            ):
                read_dataset = True
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        steps = 0
        query_started = time.monotonic()

        def progress():
            nonlocal steps
            steps += 1000
            return int(steps > MAX_OPCODES or time.monotonic() - query_started > QUERY_TIMEOUT)

        conn.set_authorizer(authorize)
        conn.set_progress_handler(progress, 1000)
        cursor = conn.execute(sql)
        if not read_dataset:
            raise DataError("查询必须读取当前数据集。")
        columns = [description[0] for description in cursor.description]
        if len(set(columns)) != len(columns) or any(len(column) > 128 for column in columns):
            raise DataError("结果列名必须唯一且不超过一百二十八字符；请设置清晰别名。")
        rows, size, truncated = [], 0, False
        for row in cursor:
            if len(rows) >= MAX_QUERY_ROWS:
                truncated = True
                break
            if any(
                isinstance(value, bytes)
                or isinstance(value, float)
                and not math.isfinite(value)
                or type(value) is int
                and abs(value) > 2**53 - 1
                for value in row
            ):
                raise DataError("查询结果必须为有限数字或文本，不能为二进制。")
            serialized = json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if any(
                isinstance(value, str) and len(value.encode("utf-8")) > MAX_CELL_BYTES
                for value in row
            ):
                raise DataError("查询结果单元格过长。")
            if size + len(serialized) > MAX_RESULT_BYTES:
                truncated = True
                break
            rows.append(list(row))
            size += len(serialized)
        result = {
            "id": uuid4().hex,
            "run_id": run["id"],
            "dataset_id": dataset["id"],
            "dataset_sha256": dataset["sha256"],
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "created_at": now(),
        }
        store.save_query(result)
        return result
    except sqlite3.Error:
        raise DataError("SQL 被拒绝、执行预算耗尽或语法无效；请简化只读查询并检查列名。") from None
    finally:
        conn.close()
