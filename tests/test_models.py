import duckdb
import pytest

from tsa import dbt_lite, raw_data


def test_all_data_tests_pass(built):
    failed = [t for t in built["tests"] if not t["passed"]]
    assert not failed and len(built["tests"]) >= 30
    kinds = {t["test"].split(":")[0] for t in built["tests"]}
    assert kinds == {"unique", "not_null", "accepted_values", "relationships", "expression_is_true", "singular"}


def test_dag_order_and_materialisation():
    models = dbt_lite.discover()
    order = dbt_lite.order(models)
    for name, m in models.items():
        for dep in m.refs:
            assert order.index(dep) < order.index(name)
    assert {m.layer for m in models.values()} == {"staging", "intermediate", "marts"}
    assert "{{" not in models["fct_orders"].sql and "raw_orders" in models["stg_orders"].sql
    # staging never reads marts; marts never read raw directly
    assert all(not m.refs for m in models.values() if m.layer == "staging")
    assert all(not m.sources for m in models.values() if m.layer == "marts")


def test_unknown_ref_rejected(tmp_path):
    (tmp_path / "marts").mkdir()
    (tmp_path / "marts" / "x.sql").write_text("select * from {{ ref('nope') }}")
    with pytest.raises(ValueError, match="nope"):
        dbt_lite.discover(tmp_path)


def test_staging_cleans_raw(ro):
    raw_status = {r[0] for r in ro.execute("select distinct status from raw_orders").fetchall()}
    assert len(raw_status) > 5
    assert {r[0] for r in ro.execute("select distinct status from stg_orders").fetchall()} == \
        {"completed", "shipped", "cancelled", "returned", "pending"}
    raw_n, raw_ids = ro.execute("select count(*), count(distinct id) from raw_payments").fetchone()
    assert raw_n > raw_ids  # duplicate loads exist in raw ...
    assert ro.execute("select count(*) from stg_payments").fetchone()[0] == raw_ids  # ... and are removed


def test_marts_reconcile(ro):
    a = ro.execute("select round(sum(net_revenue),2) from fct_orders").fetchone()[0]
    b = ro.execute("select round(sum(net_revenue),2) from fct_order_lines").fetchone()[0]
    c = ro.execute("select round(sum(lifetime_revenue),2) from dim_customers").fetchone()[0]
    assert abs(a - b) < 0.05 and abs(a - c) < 0.05
    assert "email" not in [r[0] for r in ro.execute("describe dim_customers").fetchall()]


def test_generation_is_deterministic():
    assert raw_data.generate() == raw_data.generate()


def _fresh(data):
    con = duckdb.connect()
    raw_data.load(con, data)
    dbt_lite.run_models(con)
    return {t["test"]: t for t in dbt_lite.run_tests(con)}


def test_data_tests_catch_duplicates_and_orphans():
    data = raw_data.generate()
    data["customers"].append(data["customers"][0])            # duplicate customer id
    data["orders"].append((99999, 4242, "2026-01-02 10:00:00", "completed", "web"))  # orphan customer
    data["order_items"].append((99999, 101, 1, 1000, 0))
    res = _fresh(data)
    assert not res["unique:stg_customers.customer_id"]["passed"]
    assert not res["relationships:stg_orders.customer_id"]["passed"]
    assert not res["singular:assert_paid_matches_revenue"]["passed"]  # the orphan order has no payment


def test_accepted_values_catches_new_status():
    data = raw_data.generate()
    o = data["orders"][0]
    data["orders"][0] = (o[0], o[1], o[2], "on_hold", o[4])
    assert not _fresh(data)["accepted_values:stg_orders.status"]["passed"]


def test_lineage_mermaid():
    m = dbt_lite.lineage_mermaid()
    assert m.startswith("flowchart LR") and "raw_orders --> stg_orders" in m and "fct_orders --> dim_customers" in m
