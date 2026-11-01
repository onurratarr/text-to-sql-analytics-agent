"""SQL safety guard: parse, then allow only a single read-only SELECT over allowlisted tables, with a row LIMIT.

Defence in depth: the agent also executes on a read-only DuckDB connection with a timeout, so the guard is not
the only line of defence, but it is the one that produces a clear, explainable refusal.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.qualify import qualify

DIALECT = "duckdb"
# Blocked even if a table carrying it were ever allowlisted.
DENIED_COLUMNS = {"email", "phone", "address", "password"}
# Table functions / statements that could read files or other databases.
DENIED_FUNCTIONS = ("read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
                    "glob", "sniff_csv", "query_table", "read_blob")
WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Merge, exp.Command,
               exp.Copy, exp.Attach, exp.Detach, exp.Pragma, exp.Set, exp.Use, exp.TruncateTable, exp.Transaction)


class GuardError(Exception):
    """kind: parse_error | multi_statement | not_read_only | table_not_allowed | column_denied | function_denied"""

    BLOCKING = {"multi_statement", "not_read_only", "column_denied", "function_denied"}

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind

    @property
    def blocking(self) -> bool:
        """Security violations are refused outright; the others are fed back to the model to fix."""
        return self.kind in self.BLOCKING


class ValidationError(Exception):
    pass


@dataclass
class Checked:
    sql: str
    tables: list[str]
    limit_applied: int | None


def check(sql: str, allowed_tables, max_rows: int = 200) -> Checked:
    try:
        stmts = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as e:
        raise GuardError("parse_error", f"SQL could not be parsed: {str(e).splitlines()[0]}") from e
    if len(stmts) != 1:
        raise GuardError("multi_statement", f"exactly one statement is allowed, got {len(stmts)}")
    tree = stmts[0]
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            raise GuardError("not_read_only", f"only SELECT queries are allowed ({type(node).__name__.upper()} found)")
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise GuardError("parse_error", "not a valid SELECT query")
    for f in tree.find_all(exp.Func, exp.Anonymous):
        name = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if name in DENIED_FUNCTIONS:
            raise GuardError("function_denied", f"function {name} is not allowed")
    for col in tree.find_all(exp.Column):
        if col.name.lower() in DENIED_COLUMNS:
            raise GuardError("column_denied", f"column {col.name!r} is personal data and cannot be queried")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = []
    for t in tree.find_all(exp.Table):
        if not t.name:
            raise GuardError("function_denied", "table functions are not allowed")
        if t.args.get("db") or t.args.get("catalog"):
            raise GuardError("table_not_allowed", f"qualified table names are not allowed: {t.sql()}")
        name = t.name.lower()
        if name in ctes:
            continue
        if name not in allowed_tables:
            raise GuardError("table_not_allowed",
                             f"table {t.name!r} is not available; allowed tables: {', '.join(allowed_tables)}")
        tables.append(name)
    limit = tree.args.get("limit")
    applied = None
    if limit is None:
        tree = tree.limit(max_rows, copy=False)
        applied = max_rows
    else:
        try:
            n = int(limit.expression.this)
        except (AttributeError, TypeError, ValueError):
            raise GuardError("parse_error", "LIMIT must be an integer literal")
        if n > max_rows:
            limit.set("expression", exp.Literal.number(max_rows))
            applied = max_rows
    return Checked(tree.sql(dialect=DIALECT), sorted(set(tables)), applied)


def validate(sql: str, schema: dict[str, dict[str, str]]) -> None:
    """Resolve every column against the warehouse schema before running anything (catches hallucinated columns)."""
    try:
        qualify(sqlglot.parse_one(sql, read=DIALECT), schema=schema, dialect=DIALECT,
                validate_qualify_columns=True, identify=False)
    except Exception as e:  # sqlglot raises OptimizeError for unresolved columns
        raise ValidationError(str(e).splitlines()[0]) from e
