import duckdb
import pytest

from tsa.agent import SQLAgent
from tsa.sql_models import Draft, MockSQLModel, OpenAICompatibleSQLModel, get_model, parse_period, period_where


class Scripted:
    """Fake model returning queued drafts and recording the feedback it was given."""
    name = "scripted"

    def __init__(self, *sqls):
        self.sqls, self.seen = list(sqls), []

    def generate(self, question, schema, feedback):
        self.seen.append(list(feedback))
        return Draft(self.sqls.pop(0)) if self.sqls else Draft(refusal="out of ideas")


def test_readonly_connection_rejects_writes_even_without_guard(ro):
    with pytest.raises(duckdb.Error):
        ro.execute("DELETE FROM fct_orders")
    assert ro.execute("SELECT count(*) FROM fct_orders").fetchone()[0] == 2500


def test_answer_with_limit_and_truncation(ro):
    a = SQLAgent(ro, Scripted("SELECT order_id FROM fct_orders ORDER BY 1"), max_rows=20)
    r = a.ask("list orders")
    assert r["status"] == "answered" and len(r["rows"]) == 20 and r["truncated"] and r["sql"].endswith("LIMIT 20")


def test_validation_error_is_fed_back_and_repaired(ro):
    a = SQLAgent(ro, MockSQLModel())
    r = a.ask("Who are the top 5 customers by lifetime revenue?")
    assert r["status"] == "answered" and r["repairs"] == 2
    assert [x["stage"] for x in r["attempts"]] == ["validation", "validation", "ok"]
    assert r["columns"] == ["customer_name", "lifetime_revenue"] and len(r["rows"]) == 5


def test_execution_error_feedback(ro):
    m = Scripted("SELECT CAST(status AS INTEGER) FROM fct_orders", "SELECT COUNT(*) FROM fct_orders")
    r = SQLAgent(ro, m).ask("q")
    assert r["attempts"][0]["stage"] == "execution" and "Conversion" in r["attempts"][0]["error"]
    assert m.seen[1][0]["error"] == r["attempts"][0]["error"] and r["status"] == "answered" and r["rows"] == [(2500,)]


def test_table_not_allowed_is_retryable(ro):
    m = Scripted("SELECT COUNT(*) FROM stg_orders", "SELECT COUNT(*) FROM fct_orders")
    r = SQLAgent(ro, m).ask("q")
    assert r["status"] == "answered" and r["attempts"][0]["stage"] == "guard:table_not_allowed"


def test_gives_up_after_max_attempts(ro):
    r = SQLAgent(ro, Scripted(*["SELECT nope FROM fct_orders"] * 5), max_attempts=3).ask("q")
    assert r["status"] == "failed" and len(r["attempts"]) == 3 and "3 attempts" in r["reason"]


def test_security_violation_not_retried(ro):
    m = Scripted("DELETE FROM fct_orders", "SELECT 1")
    r = SQLAgent(ro, m).ask("q")
    assert r["status"] == "blocked" and len(r["attempts"]) == 1 and not r["rows"]


def test_timeout_interrupts_long_query(ro):
    a = SQLAgent(ro, Scripted(), timeout_s=0.2)
    with pytest.raises(duckdb.Error):
        a._execute("SELECT count(*) FROM fct_orders a, fct_orders b, fct_orders c, fct_orders d")


@pytest.mark.parametrize("q,status", [
    ("Delete all cancelled orders", "blocked"),
    ("Show me customer emails for the business segment", "blocked"),
    ("What will revenue be next year?", "refused"),
    ("What is the meaning of life?", "refused"),
])
def test_mock_safety_paths(ro, q, status):
    assert SQLAgent(ro, MockSQLModel()).ask(q)["status"] == status


def test_period_parsing():
    assert parse_period("in march 2026") == ("month", 2026, 3)
    assert parse_period("q4 2025") == ("quarter", 2025, 4)
    assert period_where(("quarter", 2025, 4)) == "order_date >= DATE '2025-10-01' AND order_date < DATE '2026-01-01'"
    assert period_where(("month", 2025, 12)).endswith("DATE '2026-01-01'")
    assert period_where(None) == "TRUE"


def test_schema_context_is_marts_only(ro):
    a = SQLAgent(ro, MockSQLModel())
    assert "TABLE fct_orders" in a.schema_text and "stg_" not in a.schema_text and "email" not in a.schema_text
    assert "revenue after discounts" in a.schema_text.lower()


def test_openai_compatible_model_with_fake_transport(ro, monkeypatch):
    calls = []

    def post(payload):
        calls.append(payload)
        sql = "SELECT revenue FROM fct_orders" if len(calls) == 1 else "SELECT SUM(net_revenue) FROM fct_orders"
        return {"choices": [{"message": {"content": '{"sql": "%s"}' % sql}}]}

    r = SQLAgent(ro, OpenAICompatibleSQLModel(post=post)).ask("total revenue?")
    assert r["status"] == "answered" and r["repairs"] == 1
    assert "could not be resolved" in calls[1]["messages"][-1]["content"]
    assert "TABLE fct_orders" in calls[0]["messages"][0]["content"]
    monkeypatch.setenv("SQL_MODEL_BACKEND", "openai")
    assert isinstance(get_model(), OpenAICompatibleSQLModel)


def test_openai_refusal(ro):
    post = lambda p: {"choices": [{"message": {"content": '{"refusal": "not in schema"}'}}]}
    assert SQLAgent(ro, OpenAICompatibleSQLModel(post=post)).ask("weather?")["status"] == "refused"
