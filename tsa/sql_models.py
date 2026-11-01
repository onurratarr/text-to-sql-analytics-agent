"""SQL generators. The default MockSQLModel is offline and deterministic; OpenAICompatibleSQLModel is optional.

MockSQLModel is a pattern matcher for a fixed set of synthetic analytics questions. It is not an LLM and does not
generalise: questions phrased differently from its patterns are refused (the eval's paraphrase split shows this).
Two deliberate behaviours make it useful for exercising the agent:
  * its business glossary is stale for a few terms (e.g. "customer name" -> `name`), so some first drafts reference
    columns that don't exist and must go through the validation -> repair loop, as an LLM guessing names would;
  * imperative/adversarial requests ("delete ...", "show emails ...") are translated literally, the way a naive,
    over-compliant model might, so the SQL guard (not the model) is what stops them.
"""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
COUNTRIES = {"united states": "US", "usa": "US", "canada": "CA", "united kingdom": "GB", "uk": "GB",
             "germany": "DE", "france": "FR", "netherlands": "NL", "turkey": "TR", "türkiye": "TR", "australia": "AU"}
SEGMENTS = ("consumer", "pro", "business")
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}
# Deliberately stale for three terms (see module docstring). Correct names: customer_name, channel, lifetime_revenue.
GLOSSARY = {"customer name": "name", "channel": "sales_channel", "lifetime revenue": "total_revenue",
            "revenue": "net_revenue", "units": "quantity", "discount": "discount_amount"}
VALID_ORDER = "status IN ('completed', 'shipped')"


@dataclass
class Draft:
    sql: str | None = None
    refusal: str | None = None
    note: str = ""


def parse_period(q: str):
    m = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(20\d\d)\b", q)
    if m:
        return ("month", int(m.group(2)), MONTHS[m.group(1)])
    m = re.search(r"\bq([1-4])\s+(20\d\d)\b", q)
    if m:
        return ("quarter", int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(20\d\d)\b", q)
    return ("year", int(m.group(1)), None) if m else None


def period_where(p, col="order_date") -> str:
    if p is None:
        return "TRUE"
    kind, y, n = p
    if kind == "year":
        start, end = f"{y}-01-01", f"{y + 1}-01-01"
    elif kind == "quarter":
        sm = 3 * (n - 1) + 1
        start = f"{y}-{sm:02d}-01"
        end = f"{y + (sm + 3 > 12)}-{(sm + 3 - 1) % 12 + 1:02d}-01"
    else:
        start = f"{y}-{n:02d}-01"
        end = f"{y + (n == 12)}-{n % 12 + 1:02d}-01"
    return f"{col} >= DATE '{start}' AND {col} < DATE '{end}'"


def _n(q, default):
    m = re.search(r"\btop\s+(\d+|\w+)\b|\b(\d+|one|two|three|four|five|ten)\s+(?:customers|products)", q)
    if not m:
        return default
    tok = m.group(1) or m.group(2)
    return int(tok) if tok.isdigit() else WORDNUM.get(tok, default)


class MockSQLModel:
    name = "mock-rules"

    def generate(self, question: str, schema: str, feedback: list[dict]) -> Draft:
        if feedback:
            return self.repair(feedback[-1], schema)
        q = question.lower().strip()
        g = GLOSSARY
        p = parse_period(q)
        w = period_where(p)

        # --- imperative / adversarial requests: translated literally; the guard must stop them -----------------
        m = re.match(r"(delete|remove)\b", q)
        if m:
            status = next((s for s in ("cancelled", "returned", "pending") if s in q), None)
            return Draft("DELETE FROM fct_orders" + (f" WHERE status = '{status}'" if status else ""))
        if q.startswith(("drop", "truncate")):
            return Draft("DROP TABLE " + ("stg_orders" if "staging" in q else "fct_orders"))
        if q.startswith("update") or q.startswith("set "):
            return Draft("UPDATE fct_orders SET status = 'completed'")
        m = re.search(r"read the file (\S+)", question, re.I)
        if m:
            return Draft(f"SELECT * FROM read_csv('{m.group(1)}')")
        m = re.search(r"\braw (\w+?) table\b", q)
        if m:
            return Draft(f"SELECT * FROM raw_{m.group(1)}")
        if "email" in q:
            seg = next((s for s in SEGMENTS if s in q), None)
            return Draft("SELECT customer_name, email FROM dim_customers"
                         + (f" WHERE segment = '{seg}'" if seg else ""))
        if re.search(r"\b(forecast|predict|next year|will .* be)\b", q):
            return Draft(refusal="Forecasting is out of scope: the warehouse only holds historical orders.")

        # --- analytics questions ---------------------------------------------------------------------------------
        if "cancel" in q and re.search(r"\b(rate|share|percent|percentage|fraction)\b", q):
            return Draft(f"SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'cancelled') / COUNT(*), 2) "
                         f"AS cancellation_rate_pct FROM fct_orders WHERE {w}")
        if "how many" in q and "cancel" in q:
            return Draft(f"SELECT COUNT(*) AS cancelled_orders FROM fct_orders WHERE status = 'cancelled' AND {w}")
        if "never" in q and "order" in q and "customer" in q:
            return Draft("SELECT COUNT(*) AS customers FROM dim_customers WHERE lifetime_orders = 0")
        m = re.search(r"more than (\d+) orders", q)
        if m and "customer" in q:
            return Draft(f"SELECT COUNT(*) AS customers FROM dim_customers WHERE lifetime_orders > {m.group(1)}")
        if re.search(r"(new customers|signed up|signups)", q) and re.search(r"(each|per|by) month", q):
            return Draft("SELECT DATE_TRUNC('month', signup_date) AS signup_month, COUNT(*) AS new_customers "
                         f"FROM dim_customers WHERE {period_where(p, 'signup_date')} GROUP BY 1 ORDER BY 1")
        if re.search(r"\b(names|list)\b", q) and "customer" in q:
            seg = next((s for s in SEGMENTS if s in q), None)
            conds = [period_where(p, "signup_date")] + ([f"segment = '{seg}'"] if seg else [])
            return Draft(f"SELECT {g['customer name']} FROM dim_customers WHERE {' AND '.join(conds)} ORDER BY 1")
        if "customer" in q and re.search(r"\btop\b", q) and "revenue" in q:
            return Draft(f"SELECT {g['customer name']}, {g['lifetime revenue']} FROM dim_customers "
                         f"ORDER BY {g['lifetime revenue']} DESC LIMIT {_n(q, 5)}")
        if "product" in q and re.search(r"\b(most units|sold the most|best[- ]selling)\b", q):
            return Draft(f"SELECT product_name, SUM({g['units']}) AS units FROM fct_order_lines "
                         f"WHERE {VALID_ORDER} AND {w} GROUP BY 1 ORDER BY 2 DESC LIMIT {_n(q, 3)}")
        if "customer" in q and re.search(r"\b(per|by|each) country\b", q):
            return Draft("SELECT country, COUNT(*) AS customers FROM dim_customers GROUP BY 1 ORDER BY 2 DESC, 1")
        if "channel" in q:
            return Draft(f"SELECT {g['channel']}, ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS share_pct "
                         f"FROM fct_orders WHERE {w} GROUP BY 1 ORDER BY 2 DESC")
        if "average order value" in q:
            return Draft("SELECT order_month, avg_order_value FROM mart_monthly_revenue "
                         f"WHERE {period_where(p, 'order_month')} ORDER BY 1")
        if re.search(r"average (number of )?units per order", q):
            cond = "status = 'completed'" if "completed" in q else "TRUE"
            return Draft(f"SELECT ROUND(AVG(units), 2) AS avg_units FROM fct_orders WHERE {cond} AND {w}")
        if "discount" in q:
            return Draft(f"SELECT ROUND(SUM({g['discount']}), 2) AS total_discount FROM fct_orders WHERE {w}")
        if "month" in q and re.search(r"\b(highest|most|best)\b", q) and "revenue" in q:
            return Draft("SELECT order_month, net_revenue FROM mart_monthly_revenue "
                         f"WHERE {period_where(p, 'order_month')} ORDER BY net_revenue DESC LIMIT 1")
        if "quarter" in q and "revenue" in q:
            return Draft(f"SELECT QUARTER(order_date) AS quarter, ROUND(SUM({g['revenue']}), 2) AS net_revenue "
                         f"FROM fct_orders WHERE {w} GROUP BY 1 ORDER BY 1")
        if "segment" in q and "revenue" in q:
            return Draft(f"SELECT c.segment, ROUND(SUM(o.{g['revenue']}), 2) AS net_revenue FROM fct_orders o "
                         f"JOIN dim_customers c ON c.customer_id = o.customer_id WHERE {period_where(p, 'o.order_date')} "
                         "GROUP BY 1 ORDER BY 2 DESC LIMIT 1")
        if "revenue" in q and "category" in q:
            return Draft(f"SELECT category, ROUND(SUM({g['revenue']}), 2) AS net_revenue FROM fct_order_lines "
                         f"WHERE {w} GROUP BY 1 ORDER BY 2 DESC")
        country = next((c for n, c in COUNTRIES.items() if re.search(rf"\b{n}\b", q)), None)
        if "revenue" in q and country:
            return Draft(f"SELECT ROUND(SUM(o.{g['revenue']}), 2) AS net_revenue FROM fct_orders o "
                         f"JOIN dim_customers c ON c.customer_id = o.customer_id WHERE c.country = '{country}' "
                         f"AND {period_where(p, 'o.order_date')}")
        if re.search(r"\b(revenue|sales)\b", q) and re.search(r"\b(total|what was|how much)\b", q):
            return Draft(f"SELECT ROUND(SUM({g['revenue']}), 2) AS net_revenue FROM fct_orders WHERE {w}")
        if re.search(r"\bhow many orders\b|\bnumber of orders\b", q):
            return Draft(f"SELECT COUNT(*) AS orders FROM fct_orders WHERE {w}")
        return Draft(refusal="I couldn't map this question to the documented marts (the offline mock only "
                             "understands a fixed set of question patterns).")

    def repair(self, fb: dict, schema: str) -> Draft:
        """Fix an unresolved column by picking the closest real column of the tables the query uses."""
        m = re.search(r"Column '([^']+)' could not be resolved", fb.get("error", ""))
        if not m:
            return Draft(refusal=f"Could not repair the query: {fb.get('error')}")
        bad = m.group(1).lower()
        tables = {t.name for t in sqlglot.parse_one(fb["sql"], read="duckdb").find_all(exp.Table)}
        cols = [c for t, cs in parse_schema_text(schema).items() if t in tables for c in cs]
        best = (next((c for c in cols if c.endswith("_" + bad)), None)
                or next((c for c in cols if bad.endswith("_" + c)), None)
                or next(iter(difflib.get_close_matches(bad, cols, n=1, cutoff=0.6)), None))
        if not best:
            return Draft(refusal=f"No column similar to {bad!r} in {sorted(tables)}")
        tree = sqlglot.parse_one(fb["sql"], read="duckdb")
        for col in tree.find_all(exp.Column):
            if col.name.lower() == bad:
                col.set("this", exp.to_identifier(best))
        return Draft(tree.sql(dialect="duckdb"), note=f"repaired column {bad} -> {best}")


def parse_schema_text(schema: str) -> dict[str, list[str]]:
    out, cur = {}, None
    for line in schema.splitlines():
        if line.startswith("TABLE "):
            cur = line.split()[1]
            out[cur] = []
        elif cur and line.startswith("  "):
            out[cur].append(line.split()[0])
    return out


SYSTEM_PROMPT = """You translate analytics questions into one DuckDB SQL SELECT query over the tables below.
Rules: read-only SELECT only; use only the listed tables and columns; never select personal data such as email;
revenue means net_revenue; if the question cannot be answered from these tables, refuse.
Reply with JSON only: {"sql": "..."} or {"refusal": "reason"}.

SCHEMA:
"""


class OpenAICompatibleSQLModel:
    """Any OpenAI-compatible /chat/completions endpoint. Errors from the guard/validator/DB are fed back verbatim."""

    def __init__(self, post=None):
        self.base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.name = f"openai-compatible:{self.model}"
        self._post = post

    def _complete(self, payload):
        if self._post:
            return self._post(payload)
        import httpx  # optional dependency, only needed for this backend

        r = httpx.post(f"{self.base}/chat/completions", json=payload, timeout=60,
                       headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"})
        r.raise_for_status()
        return r.json()

    def generate(self, question: str, schema: str, feedback: list[dict]) -> Draft:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT + schema}, {"role": "user", "content": question}]
        for fb in feedback:
            msgs += [{"role": "assistant", "content": json.dumps({"sql": fb["sql"]})},
                     {"role": "user", "content": f"That query failed: {fb['error']}. Return a corrected query."}]
        data = self._complete({"model": self.model, "messages": msgs, "temperature": 0,
                               "response_format": {"type": "json_object"}})
        text = data["choices"][0]["message"]["content"]
        try:
            obj = json.loads(re.sub(r"^```(json)?|```$", "", text.strip()))
        except json.JSONDecodeError:
            return Draft(sql=text.strip())
        return Draft(sql=obj.get("sql"), refusal=obj.get("refusal"))


def get_model():
    return OpenAICompatibleSQLModel() if os.environ.get("SQL_MODEL_BACKEND", "mock") == "openai" else MockSQLModel()
