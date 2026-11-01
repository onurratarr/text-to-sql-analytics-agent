"""CLI: build the warehouse, ask questions, run the eval, print lineage."""
from __future__ import annotations

import argparse
import json
import sys

from . import dbt_lite, evaluate, warehouse
from .agent import SQLAgent

RESULTS = dbt_lite.ROOT / "results"


def cmd_build(a):
    r = warehouse.build()
    print(f"raw tables loaded: {r['raw']}")
    for m in r["models"]:
        print(f"  {m['layer']:<13} {m['model']:<22} {m['materialized']:<6} {m['rows']:>6} rows")
    failed = [t for t in r["tests"] if not t["passed"]]
    print(f"data tests: {len(r['tests']) - len(failed)}/{len(r['tests'])} passed")
    for t in failed:
        print(f"  FAIL {t['test']} ({t['failures']} rows)")
    if failed:
        sys.exit(1)


def _show(r):
    print(f"status: {r['status']}  (model: {r['model']}, {r['elapsed_ms']} ms)")
    for i, at in enumerate(r["attempts"], 1):
        print(f"  attempt {i}: {at['sql']}")
        print(f"     -> {at['stage']}" + (f": {at['error']}" if at["error"] else "")
              + (f"  [{at['note']}]" if at.get("note") else ""))
    if r["reason"]:
        print(f"reason: {r['reason']}")
    if r["status"] == "answered":
        print("final SQL: " + r["sql"])
        print(" | ".join(r["columns"]))
        for row in r["rows"][:15]:
            print(" | ".join(str(v) for v in row))
        if len(r["rows"]) > 15:
            print(f"... {len(r['rows'])} rows")


def cmd_ask(a):
    agent = SQLAgent()
    for q in a.question:
        print(f"Q: {q}")
        _show(agent.ask(q))
        print()


def cmd_eval(a):
    b = warehouse.build()
    rows, s = evaluate.run()
    md = evaluate.results_markdown(rows, s, b)
    print(md)
    if a.write:
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / "RESULTS.md").write_text(md)
        (RESULTS / "results.json").write_text(json.dumps({"summary": s, "questions": rows}, indent=1))
    if a.check and not (s["gate_passed"] and b["tests_passed"]):
        sys.exit("eval gate failed")


def main(argv=None):
    p = argparse.ArgumentParser(prog="tsa")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="generate raw data, run models, run data tests").set_defaults(fn=cmd_build)
    q = sub.add_parser("ask", help="ask one or more questions")
    q.add_argument("question", nargs="+")
    q.set_defaults(fn=cmd_ask)
    e = sub.add_parser("eval", help="execution-accuracy eval on the synthetic question set")
    e.add_argument("--write", action="store_true")
    e.add_argument("--check", action="store_true", help="exit 1 if the core/safety gate or a data test fails")
    e.set_defaults(fn=cmd_eval)
    sub.add_parser("lineage", help="print model lineage as mermaid").set_defaults(
        fn=lambda a: print(dbt_lite.lineage_mermaid()))
    sub.add_parser("schema", help="print the schema context given to the SQL model").set_defaults(
        fn=lambda a: print(warehouse.schema_context(warehouse.connect_readonly())))
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
