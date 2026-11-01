"""Build the synthetic warehouse (raw load -> models -> data tests) and open read-only connections."""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

from . import dbt_lite, raw_data

DEFAULT_DB = Path(os.environ.get("WAREHOUSE_PATH") or dbt_lite.ROOT / "warehouse" / "juniper.duckdb")
# The only relations the agent may query: the documented marts layer.
ALLOWED_TABLES = ("fct_orders", "fct_order_lines", "dim_customers", "dim_products", "mart_monthly_revenue")


def build(path: Path = DEFAULT_DB) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    try:
        loaded = raw_data.load(con)
        models = dbt_lite.run_models(con)
        tests = dbt_lite.run_tests(con)
    finally:
        con.close()
    return {"path": str(path), "raw": loaded, "models": models, "tests": tests,
            "tests_passed": all(t["passed"] for t in tests)}


def connect_readonly(path: Path = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    if not Path(path).exists():
        build(path)
    return duckdb.connect(str(path), read_only=True)


def schema_info(con, tables=ALLOWED_TABLES) -> dict[str, dict[str, str]]:
    rows = con.execute(
        "select table_name, column_name, data_type from information_schema.columns "
        f"where table_name in ({', '.join('?' * len(tables))}) order by table_name, ordinal_position", list(tables)
    ).fetchall()
    out: dict[str, dict[str, str]] = {t: {} for t in tables}
    for t, c, d in rows:
        out[t][c] = d
    return out


def schema_context(con, tables=ALLOWED_TABLES) -> str:
    """Schema text given to the SQL model: tables, columns, types and the column docs from schema.yml."""
    docs = {m["name"]: m for m in dbt_lite.load_schema()["models"]}
    parts = []
    for t, cols in schema_info(con, tables).items():
        d = docs.get(t, {})
        cdocs = {c["name"]: c.get("description", "") for c in d.get("columns", [])}
        parts.append(f"TABLE {t}" + (f" -- {d['description']}" if d.get("description") else ""))
        parts += [f"  {c} {typ}" + (f" -- {cdocs[c]}" if cdocs.get(c) else "") for c, typ in cols.items()]
    return "\n".join(parts)
