import pytest

from tsa import evaluate
from tsa.agent import SQLAgent
from tsa.sql_models import MockSQLModel


@pytest.fixture(scope="module")
def run(built):
    from tsa import warehouse
    con = warehouse.connect_readonly(built["path"])
    rows, s = evaluate.run(SQLAgent(con, MockSQLModel()))
    yield rows, s
    con.close()


def test_results_match():
    m = evaluate.results_match
    assert m([(1, 2.004)], [(1, 2.0)], True)
    assert m([("a", 1), ("b", 2)], [("b", 2), ("a", 1)], False)
    assert not m([("a", 1), ("b", 2)], [("b", 2), ("a", 1)], True)
    assert m([(5, "x")], [("x", 5)], True)  # column order ignored
    assert not m([(1,)], [(1,), (2,)], False)
    assert not m([(1, 2)], [(1,)], False)
    assert m([("2026-01-01T00:00:00",)], [("2026-01-01",)], True)


def test_gate_passes(run):
    rows, s = run
    assert s["gate_passed"], [r for r in rows if not r["correct"] and r["split"] != "paraphrase"]
    assert s["core"]["n"] == 20 and s["safety"]["n"] == 7


def test_self_correction_is_exercised(run):
    rows, s = run
    assert s["answered_after_repair"] >= 1 and s["repaired_and_correct"] == s["answered_after_repair"]


def test_paraphrase_split_exposes_mock_limits(run):
    rows, s = run
    para = [r for r in rows if r["split"] == "paraphrase"]
    assert s["paraphrase"]["correct"] < s["paraphrase"]["n"]  # the mock is not a general model
    assert any(r["status"] == "answered" and not r["correct"] for r in para)  # a confidently wrong answer is scored wrong


def test_safety_rows_return_nothing(run):
    rows, _ = run
    assert all(r["status"] != "answered" for r in rows if r["split"] == "safety")


def test_gold_refs_resolved():
    qs = {q["id"]: q for q in evaluate.load_questions()}
    assert qs["p04"]["gold"] == qs["c05"]["gold"] and qs["p04"]["ordered"] is True


def test_markdown(run, built):
    rows, s = run
    md = evaluate.results_markdown(rows, s, built)
    assert "Synthetic-data results" in md and "| c01 | core |" in md and "Data quality tests" in md
