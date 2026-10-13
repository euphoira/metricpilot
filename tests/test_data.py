import time

import pytest

from metricpilot import data
from metricpilot.data import DataError, Store, execute_select, parse_csv, profile
from metricpilot.demo import CSV, seed_demo


@pytest.fixture
def workspace(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    dataset = seed_demo(store)
    run = store.create_run(dataset["id"], "总收入是多少？", "demo")
    return store, dataset, run


def test_csv_infers_nullable_types_without_losing_identifier_zeros():
    columns, rows = parse_csv("id,amount,count,note\n001,1.5,2,\n002,,3,hello\n")
    assert [column["type"] for column in columns] == ["TEXT", "REAL", "INTEGER", "TEXT"]
    assert rows == [["001", 1.5, 2, None], ["002", None, 3, "hello"]]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "name\n",
        "a,A\n1,2\n",
        "a;DROP TABLE x\n1\n",
        "a,b\n1\n",
        "a\n1,2\n",
        'a\n"unclosed',
        "a\n\x00\n",
        "a\n" + "x" * 4097,
        ",".join("c" + str(i) for i in range(31)) + "\n" + ",".join("1" for _ in range(31)),
    ],
)
def test_invalid_csv_is_rejected(text):
    with pytest.raises(DataError):
        parse_csv(text)


def test_csv_row_and_byte_budgets(monkeypatch):
    monkeypatch.setattr(data, "MAX_ROWS", 2)
    with pytest.raises(DataError):
        parse_csv("a\n1\n2\n3\n")
    monkeypatch.setattr(data, "MAX_CSV_BYTES", 5)
    with pytest.raises(DataError):
        parse_csv("a\nhello\n")


def test_large_integers_and_nonfinite_text_are_not_silently_rounded():
    columns, rows = parse_csv("identifier,value\n9007199254740993,NaN\n9007199254740994,Infinity\n")
    assert [column["type"] for column in columns] == ["TEXT", "TEXT"]
    assert rows[0] == ["9007199254740993", "NaN"]


def test_generated_table_id_ignores_user_supplied_label(workspace):
    store, _, _ = workspace
    dataset = store.import_csv("users; DROP TABLE runs; --", "value\n1\n")
    assert dataset["table_name"].startswith("d_")
    assert ";" not in dataset["table_name"]
    assert len(store.runs()) == 1


def test_count_star_sum_profile_and_persisted_provenance(workspace):
    store, dataset, run = workspace
    query = execute_select(
        store, run, f"SELECT COUNT(*) AS n,SUM(revenue) AS total FROM {dataset['table_name']}"
    )
    assert query["rows"] == [[8, 8000]]
    assert query["dataset_sha256"] == dataset["sha256"]
    assert not query["truncated"]
    assert Store(store.path).queries(run["id"]) == [query]
    result = profile(*store.dataset(dataset["id"]))
    assert result["row_count"] == 8
    assert next(c for c in result["columns"] if c["name"] == "revenue")["min"] == 600


@pytest.mark.parametrize(
    "template",
    [
        "DELETE FROM {table}",
        "ATTACH DATABASE '/tmp/private' AS private",
        "PRAGMA table_info({table})",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM sqlite_schema",
        "SELECT * FROM pragma_table_info('{table}')",
        "SELECT load_extension('anything') FROM {table}",
        "SELECT readfile('/etc/passwd') FROM {table}",
        "SELECT randomblob(1000000) FROM {table}",
        "SELECT sqlite_version() FROM {table}",
        "SELECT COUNT(*) FROM {table}; DROP TABLE {table}",
        "SELECT (SELECT name FROM sqlite_master LIMIT 1) FROM {table}",
        "SELECT 123",
        "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT * FROM x",
    ],
)
def test_sql_boundary_denies_io_metadata_writes_and_unbounded_operations(workspace, template):
    store, dataset, run = workspace
    with pytest.raises(DataError):
        execute_select(store, run, template.format(table=dataset["table_name"]))
    assert store.queries(run["id"]) == []
    assert execute_select(store, run, f"SELECT COUNT(*) AS n FROM {dataset['table_name']}")[
        "rows"
    ] == [[8]]


def test_second_dataset_cannot_be_accessed(workspace):
    store, _, run = workspace
    other = store.import_csv("Other", "private_value\n123\n")
    with pytest.raises(DataError):
        execute_select(store, run, f"SELECT * FROM {other['table_name']}")


def test_query_rows_are_bounded_and_truncation_is_explicit(workspace, monkeypatch):
    store, dataset, run = workspace
    monkeypatch.setattr(data, "MAX_QUERY_ROWS", 2)
    query = execute_select(store, run, f"SELECT order_id FROM {dataset['table_name']}")
    assert len(query["rows"]) == query["row_count"] == 2
    assert query["truncated"]


def test_progress_handler_interrupts_expensive_queries(workspace, monkeypatch):
    store, dataset, run = workspace
    table = dataset["table_name"]
    monkeypatch.setattr(data, "MAX_OPCODES", 1000)
    started = time.monotonic()
    with pytest.raises(DataError):
        execute_select(
            store, run, f"SELECT SUM(a.revenue) FROM {table} a,{table} b,{table} c,{table} d"
        )
    assert time.monotonic() - started < 5
    assert store.queries(run["id"]) == []


@pytest.mark.parametrize(
    "select",
    [
        "revenue,revenue",
        "CAST(revenue AS BLOB)",
        "1e999 AS x",
        "9007199254740993 AS x",
    ],
)
def test_ambiguous_nonfinite_binary_results_cannot_be_saved(workspace, select):
    store, dataset, run = workspace
    with pytest.raises(DataError):
        execute_select(store, run, f"SELECT {select} FROM {dataset['table_name']}")


def test_result_budget_and_dataset_capacity(workspace, monkeypatch):
    store, dataset, run = workspace
    monkeypatch.setattr(data, "MAX_RESULT_BYTES", 20)
    query = execute_select(store, run, f"SELECT order_id FROM {dataset['table_name']}")
    assert query["truncated"] and len(query["rows"]) <= 2
    for i in range(19):
        store.import_csv(f"Dataset {i}", CSV)
    with pytest.raises(DataError):
        store.import_csv("Over capacity", CSV)
