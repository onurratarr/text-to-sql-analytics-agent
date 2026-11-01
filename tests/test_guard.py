import pytest

from tsa.guard import GuardError, ValidationError, check, validate
from tsa.warehouse import ALLOWED_TABLES

BAD = [
    ("DELETE FROM fct_orders", "not_read_only"),
    ("UPDATE fct_orders SET status = 'x'", "not_read_only"),
    ("INSERT INTO fct_orders SELECT * FROM fct_orders", "not_read_only"),
    ("DROP TABLE fct_orders", "not_read_only"),
    ("CREATE TABLE t AS SELECT 1", "not_read_only"),
    ("ATTACH 'other.db' AS o", "not_read_only"),
    ("COPY fct_orders TO 'out.csv'", "not_read_only"),
    ("PRAGMA database_list", "not_read_only"),
    ("SELECT 1; DROP TABLE fct_orders", "multi_statement"),
    ("SELECT * FROM stg_customers", "table_not_allowed"),
    ("SELECT * FROM raw_payments", "table_not_allowed"),
    ("SELECT * FROM main.fct_orders", "table_not_allowed"),
    ("SELECT * FROM information_schema.tables", "table_not_allowed"),
    ("SELECT customer_name, email FROM dim_customers", "column_denied"),
    ("SELECT * FROM read_csv('/etc/passwd')", "function_denied"),
    ("SELECT * FROM read_parquet('s3://bucket/x.parquet')", "function_denied"),
    ("WITH x AS (DELETE FROM fct_orders RETURNING *) SELECT * FROM x", None),  # either parse or write error
    ("selec * frm fct_orders", "parse_error"),
]


@pytest.mark.parametrize("sql,kind", BAD)
def test_rejected(sql, kind):
    with pytest.raises(GuardError) as e:
        check(sql, ALLOWED_TABLES)
    if kind:
        assert e.value.kind == kind


def test_blocking_vs_retryable():
    assert GuardError("not_read_only", "").blocking and GuardError("column_denied", "").blocking
    assert not GuardError("table_not_allowed", "").blocking and not GuardError("parse_error", "").blocking


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM fct_orders",
    "WITH m AS (SELECT * FROM mart_monthly_revenue) SELECT * FROM m",
    "SELECT c.country, SUM(o.net_revenue) FROM fct_orders o JOIN dim_customers c USING (customer_id) GROUP BY 1",
    "SELECT category FROM dim_products UNION SELECT category FROM fct_order_lines",
])
def test_allowed(sql):
    assert check(sql, ALLOWED_TABLES).sql


def test_limit_added_and_capped():
    c = check("SELECT * FROM fct_orders", ALLOWED_TABLES, max_rows=50)
    assert c.sql.endswith("LIMIT 50") and c.limit_applied == 50
    c = check("SELECT * FROM fct_orders LIMIT 5000", ALLOWED_TABLES, max_rows=50)
    assert c.sql.endswith("LIMIT 50")
    c = check("SELECT * FROM fct_orders LIMIT 10", ALLOWED_TABLES, max_rows=50)
    assert c.sql.endswith("LIMIT 10") and c.limit_applied is None


def test_validate_catches_unknown_columns(ro):
    from tsa.warehouse import schema_info
    schema = schema_info(ro)
    validate("SELECT net_revenue FROM fct_orders", schema)
    with pytest.raises(ValidationError, match="revenue"):
        validate("SELECT revenue FROM fct_orders", schema)
    with pytest.raises(ValidationError):
        validate("SELECT o.nope FROM fct_orders o JOIN dim_customers c USING (customer_id)", schema)
