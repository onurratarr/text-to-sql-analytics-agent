"""Schema-aware text-to-SQL agent: draft -> guard -> validate -> execute (read-only, timeout) -> self-correct."""
from __future__ import annotations

import threading
import time
from datetime import date, datetime
from decimal import Decimal

from . import warehouse
from .guard import GuardError, ValidationError, check, validate
from .sql_models import get_model


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


class SQLAgent:
    def __init__(self, con=None, model=None, max_attempts: int = 3, max_rows: int = 200, timeout_s: float = 5.0,
                 allowed_tables=warehouse.ALLOWED_TABLES):
        self.con = con or warehouse.connect_readonly()
        self.model = model or get_model()
        self.max_attempts, self.max_rows, self.timeout_s = max_attempts, max_rows, timeout_s
        self.allowed = tuple(allowed_tables)
        self.schema = warehouse.schema_info(self.con, self.allowed)
        self.schema_text = warehouse.schema_context(self.con, self.allowed)

    def _execute(self, sql):
        timer = threading.Timer(self.timeout_s, self.con.interrupt)
        timer.start()
        try:
            cur = self.con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [tuple(_plain(v) for v in r) for r in cur.fetchall()]
        finally:
            timer.cancel()
        return cols, rows

    def ask(self, question: str) -> dict:
        t0 = time.perf_counter()
        out = {"question": question, "model": self.model.name, "status": "failed", "sql": None, "attempts": [],
               "columns": [], "rows": [], "reason": None}
        feedback: list[dict] = []
        for _ in range(self.max_attempts):
            draft = self.model.generate(question, self.schema_text, feedback)
            if draft.refusal or not draft.sql:
                out.update(status="refused", reason=draft.refusal or "model returned no SQL")
                break
            att = {"sql": draft.sql, "stage": None, "error": None, "note": draft.note}
            out["attempts"].append(att)
            try:
                checked = check(draft.sql, self.allowed, self.max_rows)
                validate(checked.sql, self.schema)
                cols, rows = self._execute(checked.sql)
            except GuardError as e:
                att.update(stage=f"guard:{e.kind}", error=str(e))
                if e.blocking:
                    out.update(status="blocked", reason=str(e))
                    break
            except ValidationError as e:
                att.update(stage="validation", error=str(e))
            except Exception as e:  # DuckDB binder/runtime errors and timeouts
                att.update(stage="execution", error=str(e).splitlines()[0])
            else:
                att["stage"] = "ok"
                out.update(status="answered", sql=checked.sql, columns=cols, rows=rows,
                           truncated=checked.limit_applied is not None and len(rows) >= self.max_rows)
                break
            feedback.append({"sql": draft.sql, "error": att["error"]})
        else:
            out["reason"] = f"no valid query after {self.max_attempts} attempts"
        out["repairs"] = max(0, len(out["attempts"]) - 1) if out["status"] == "answered" else None
        out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out
