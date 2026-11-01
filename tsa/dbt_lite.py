"""A tiny dbt-style runner: ref()/source() resolution, DAG ordering, materialisation by layer, generic + singular
data tests and a lineage diagram. It covers the subset of dbt this project needs; the SQL files use dbt syntax so
they could move to dbt-duckdb unchanged apart from a profiles/project file."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
REF = re.compile(r"\{\{\s*ref\(\s*['\"](\w+)['\"]\s*\)\s*\}\}")
SOURCE = re.compile(r"\{\{\s*source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)\s*\}\}")
MATERIALIZATION = {"staging": "view", "intermediate": "view", "marts": "table"}


@dataclass
class Model:
    name: str
    layer: str
    raw_sql: str
    refs: set = field(default_factory=set)
    sources: set = field(default_factory=set)

    @property
    def sql(self):
        s = REF.sub(lambda m: m.group(1), self.raw_sql)
        return SOURCE.sub(lambda m: f"{m.group(1)}_{m.group(2)}", s)


def discover(models_dir: Path = ROOT / "models") -> dict[str, Model]:
    models = {}
    for p in sorted(models_dir.glob("*/*.sql")):
        raw = p.read_text()
        models[p.stem] = Model(p.stem, p.parent.name, raw, set(REF.findall(raw)),
                               {f"{a}_{b}" for a, b in SOURCE.findall(raw)})
    for m in models.values():
        missing = m.refs - set(models)
        if missing:
            raise ValueError(f"{m.name} refs unknown model(s): {sorted(missing)}")
    return models


def order(models: dict[str, Model]) -> list[str]:
    return list(TopologicalSorter({n: m.refs for n, m in models.items()}).static_order())


def run_models(con: duckdb.DuckDBPyConnection, models: dict[str, Model] | None = None) -> list[dict]:
    models = models or discover()
    out = []
    for name in order(models):
        m = models[name]
        kind = MATERIALIZATION.get(m.layer, "view")
        con.execute(f"DROP VIEW IF EXISTS {name}")
        con.execute(f"DROP TABLE IF EXISTS {name}")
        con.execute(f"CREATE {kind.upper()} {name} AS {m.sql}")
        rows = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        out.append({"model": name, "layer": m.layer, "materialized": kind, "rows": rows})
    return out


def _generic_sql(model, column, test):
    name, args = (test, {}) if isinstance(test, str) else next(iter(test.items()))
    if name == "unique":
        return name, f"select {column}, count(*) n from {model} where {column} is not null group by 1 having count(*) > 1"
    if name == "not_null":
        return name, f"select * from {model} where {column} is null"
    if name == "accepted_values":
        vals = ", ".join("'" + str(v).replace("'", "''") + "'" for v in args["values"])
        return name, f"select {column} from {model} where {column} is not null and {column} not in ({vals})"
    if name == "relationships":
        return name, (f"select c.{column} from {model} c left join {args['to']} p on c.{column} = p.{args['field']} "
                      f"where c.{column} is not null and p.{args['field']} is null")
    if name == "expression_is_true":
        return name, f"select * from {model} where not ({args['expression']})"
    raise ValueError(f"unknown test {name}")


def load_schema(path: Path = ROOT / "models" / "schema.yml") -> dict:
    return yaml.safe_load(path.read_text())


def run_tests(con, schema: dict | None = None, data_tests_dir: Path = ROOT / "data_tests") -> list[dict]:
    schema = schema or load_schema()
    results = []
    for m in schema["models"]:
        for col in m.get("columns", []):
            for t in col.get("tests", []):
                name, sql = _generic_sql(m["name"], col["name"], t)
                n = con.execute(f"select count(*) from ({sql})").fetchone()[0]
                results.append({"test": f"{name}:{m['name']}.{col['name']}", "failures": n, "passed": n == 0})
    for p in sorted(data_tests_dir.glob("*.sql")):
        sql = Model(p.stem, "test", p.read_text()).sql
        n = con.execute(f"select count(*) from ({sql})").fetchone()[0]
        results.append({"test": f"singular:{p.stem}", "failures": n, "passed": n == 0})
    return results


def lineage_mermaid(models: dict[str, Model] | None = None) -> str:
    models = models or discover()
    lines = ["flowchart LR"]
    for layer in ("raw", "staging", "intermediate", "marts"):
        names = sorted({s for m in models.values() for s in m.sources}) if layer == "raw" else \
            [n for n, m in models.items() if m.layer == layer]
        lines.append(f"    subgraph {layer}")
        lines += [f"        {n}" for n in names]
        lines.append("    end")
    for n, m in models.items():
        lines += [f"    {d} --> {n}" for d in sorted(m.sources | m.refs)]
    return "\n".join(lines)
