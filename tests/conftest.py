import pytest

from tsa import warehouse


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("SQL_MODEL_BACKEND", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    path = tmp_path_factory.mktemp("wh") / "test.duckdb"
    return warehouse.build(path)


@pytest.fixture
def ro(built):
    con = warehouse.connect_readonly(built["path"])
    yield con
    con.close()
