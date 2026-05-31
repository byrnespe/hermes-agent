#!/usr/bin/env python3
"""
Data Visualization Tools

Three tools for the data-viz agent workflow:
  - read_local_data: read a local CSV/JSON/Excel file → schema + preview + records
  - fetch_web_data:  fetch CSV/JSON from a URL → schema + preview + records
  - make_card:       render an interactive Plotly HTML chart and save it to disk
"""

import json
import logging
import os
import re
import datetime
from typing import Any, Dict, List, Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

# Default output directory.  The user runs Hermes under WSL, so cards land on
# the Windows Desktop via the /mnt/c mount.
_DEFAULT_CARDS_DIR = "/mnt/c/Users/pbyrnes/Desktop/cards"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_path(path: str) -> str:
    """Expand ~ and resolve the path; reject traversal attempts."""
    expanded = os.path.expanduser(path)
    resolved = os.path.realpath(expanded)
    # Guard against traversal outside allowed roots
    allowed_roots = (
        "/mnt/c/Users/pbyrnes",
        "/tmp",
        "/home",
        "/root",
    )
    if not any(resolved.startswith(r) for r in allowed_roots):
        raise ValueError(f"Path outside allowed roots: {resolved!r}")
    return resolved


def _infer_schema(records: List[Dict]) -> Dict[str, str]:
    """Return {col: type_str} for the first 20 records."""
    if not records:
        return {}
    cols: Dict[str, str] = {}
    for row in records[:20]:
        for k, v in row.items():
            if k in cols:
                continue
            if isinstance(v, bool):
                cols[k] = "boolean"
            elif isinstance(v, int):
                cols[k] = "integer"
            elif isinstance(v, float):
                cols[k] = "float"
            else:
                cols[k] = "string"
    return cols


def _records_to_result(records: List[Dict], source: str) -> str:
    """Serialize to the standard tool-result envelope."""
    schema = _infer_schema(records)
    preview = records[:5]
    return json.dumps({
        "success": True,
        "source": source,
        "row_count": len(records),
        "schema": schema,
        "preview": preview,
        "records": records,
    })


# ---------------------------------------------------------------------------
# Tool: read_local_data
# ---------------------------------------------------------------------------

def read_local_data(path: str, task_id: str = None) -> str:
    """
    Read a local data file (CSV, JSON, Excel, TSV) and return
    schema + preview + full records as JSON.
    """
    try:
        safe = _safe_path(path)
    except ValueError as exc:
        return json.dumps({"success": False, "error": str(exc)})

    if not os.path.isfile(safe):
        return json.dumps({"success": False, "error": f"File not found: {safe!r}"})

    ext = os.path.splitext(safe)[1].lower()

    try:
        if ext == ".json":
            with open(safe, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, list):
                records = raw
            elif isinstance(raw, dict):
                # Try common wrapping keys
                for key in ("data", "rows", "results", "records", "items"):
                    if key in raw and isinstance(raw[key], list):
                        records = raw[key]
                        break
                else:
                    records = [raw]
            else:
                return json.dumps({"success": False, "error": "JSON root must be a list or object"})

        elif ext in (".csv", ".tsv"):
            import csv
            delimiter = "\t" if ext == ".tsv" else ","
            with open(safe, "r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh, delimiter=delimiter)
                records = [dict(row) for row in reader]

        elif ext in (".xlsx", ".xls"):
            try:
                import openpyxl  # noqa: F401
                import pandas as pd
                df = pd.read_excel(safe)
                records = df.where(pd.notnull(df), None).to_dict(orient="records")
            except ImportError:
                return json.dumps({
                    "success": False,
                    "error": "openpyxl/pandas not installed. Install with: pip install openpyxl pandas",
                })

        else:
            # Attempt CSV as fallback
            import csv
            with open(safe, "r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                records = [dict(row) for row in reader]
            if not records:
                return json.dumps({"success": False, "error": f"Unsupported or empty file: {ext!r}"})

    except Exception as exc:
        return json.dumps({"success": False, "error": f"Failed to read {safe!r}: {exc}"})

    return _records_to_result(records, safe)


# ---------------------------------------------------------------------------
# Tool: fetch_web_data
# ---------------------------------------------------------------------------

def fetch_web_data(url: str, task_id: str = None) -> str:
    """
    Fetch data from a URL (JSON API or CSV endpoint) and return
    schema + preview + full records as JSON.
    """
    import urllib.request
    import urllib.error

    # Basic URL validation — no file:// or localhost traversal
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return json.dumps({"success": False, "error": "URL must start with http:// or https://"})

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HermesDataViz/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw_bytes = resp.read()
    except urllib.error.URLError as exc:
        return json.dumps({"success": False, "error": f"Network error fetching {url!r}: {exc}"})
    except Exception as exc:
        return json.dumps({"success": False, "error": f"Failed to fetch {url!r}: {exc}"})

    raw_text = raw_bytes.decode("utf-8", errors="replace")

    # Detect format: prefer content-type, fall back to URL extension / sniffing
    is_json = "json" in content_type or url.endswith(".json") or raw_text.lstrip().startswith(("{", "["))
    is_csv = "csv" in content_type or url.endswith(".csv") or (not is_json and "," in raw_text[:500])

    try:
        if is_json:
            raw = json.loads(raw_text)
            if isinstance(raw, list):
                records = raw
            elif isinstance(raw, dict):
                for key in ("data", "rows", "results", "records", "items"):
                    if key in raw and isinstance(raw[key], list):
                        records = raw[key]
                        break
                else:
                    records = [raw]
            else:
                return json.dumps({"success": False, "error": "JSON root must be list or object"})

        elif is_csv:
            import csv
            import io
            reader = csv.DictReader(io.StringIO(raw_text))
            records = [dict(row) for row in reader]

        else:
            return json.dumps({"success": False, "error": "Could not detect CSV or JSON format from response"})

    except Exception as exc:
        return json.dumps({"success": False, "error": f"Failed to parse response from {url!r}: {exc}"})

    return _records_to_result(records, url)


# ---------------------------------------------------------------------------
# Tool: make_card
# ---------------------------------------------------------------------------

def make_card(
    records: Any,
    chart_type: str,
    x: str,
    y: str,
    title: str,
    source: str,
    cards_dir: str = _DEFAULT_CARDS_DIR,
    task_id: str = None,
) -> str:
    """
    Build an interactive HTML chart (Plotly) from *records* and save it to
    *cards_dir*.  Returns the saved file path + a plain-English takeaway.
    """
    try:
        import plotly.graph_objects as go
        import plotly.express as px
    except ImportError:
        return json.dumps({
            "success": False,
            "error": "plotly not installed. Install with: pip install plotly",
        })

    # Accept records as a JSON string or a Python list
    if isinstance(records, str):
        try:
            records = json.loads(records)
        except json.JSONDecodeError as exc:
            return json.dumps({"success": False, "error": f"records is not valid JSON: {exc}"})

    if not isinstance(records, list) or not records:
        return json.dumps({"success": False, "error": "records must be a non-empty list of dicts"})

    # Validate columns
    available = list(records[0].keys())
    if x not in available:
        return json.dumps({
            "success": False,
            "error": f"x column {x!r} not found. Available: {available}",
        })
    # y can be a single column name or comma-separated list for multi-series
    y_cols = [c.strip() for c in y.split(",")] if "," in y else [y]
    missing = [c for c in y_cols if c not in available]
    if missing:
        return json.dumps({
            "success": False,
            "error": f"y column(s) {missing!r} not found. Available: {available}",
        })

    # Coerce numeric y columns
    def _coerce(val):
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return val

    # Build figure
    chart_type = chart_type.lower().strip()
    source_label = f"Source: {source}"

    try:
        if chart_type == "line":
            fig = go.Figure()
            for col in y_cols:
                x_vals = [r.get(x) for r in records]
                y_vals = [_coerce(r.get(col)) for r in records]
                fig.add_trace(go.Scatter(x=x_vals, y=y_vals, mode="lines+markers", name=col))

        elif chart_type == "bar":
            fig = go.Figure()
            for col in y_cols:
                x_vals = [r.get(x) for r in records]
                y_vals = [_coerce(r.get(col)) for r in records]
                fig.add_trace(go.Bar(x=x_vals, y=y_vals, name=col))

        elif chart_type == "scatter":
            if len(y_cols) > 1:
                y_col = y_cols[0]  # scatter uses first y only
            else:
                y_col = y_cols[0]
            x_vals = [r.get(x) for r in records]
            y_vals = [_coerce(r.get(y_col)) for r in records]
            fig = go.Figure(go.Scatter(x=x_vals, y=y_vals, mode="markers", name=y_col))

        elif chart_type == "pie":
            labels = [r.get(x) for r in records]
            values = [_coerce(r.get(y_cols[0])) for r in records]
            fig = go.Figure(go.Pie(labels=labels, values=values))

        else:
            return json.dumps({
                "success": False,
                "error": f"Unknown chart_type {chart_type!r}. Use: line, bar, scatter, pie",
            })

        fig.update_layout(
            title={"text": title, "x": 0.5, "xanchor": "center"},
            annotations=[{
                "text": source_label,
                "xref": "paper", "yref": "paper",
                "x": 1, "y": -0.12,
                "showarrow": False,
                "font": {"size": 11, "color": "#888"},
                "xanchor": "right",
            }],
            template="plotly_white",
        )

    except Exception as exc:
        return json.dumps({"success": False, "error": f"Chart build failed: {exc}"})

    # Save card
    os.makedirs(cards_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = re.sub(r"[^\w\-]", "_", title)[:60]
    filename = f"{safe_title}_{timestamp}.html"
    out_path = os.path.join(cards_dir, filename)

    try:
        fig.write_html(out_path, include_plotlyjs="cdn", full_html=True)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"Failed to write HTML to {out_path!r}: {exc}"})

    # Compute a simple takeaway from the data
    takeaway = _compute_takeaway(records, x, y_cols[0], chart_type)

    return json.dumps({
        "success": True,
        "saved_path": out_path,
        "chart_type": chart_type,
        "title": title,
        "source": source,
        "takeaway": takeaway,
    })


def _compute_takeaway(records: List[Dict], x: str, y: str, chart_type: str) -> str:
    """Generate a one-line plain-English observation from the data."""
    try:
        numeric_vals = []
        for r in records:
            v = r.get(y)
            try:
                numeric_vals.append(float(v))
            except (TypeError, ValueError):
                pass

        if not numeric_vals:
            return f"Chart shows {y} by {x} ({len(records)} data points)."

        mn, mx = min(numeric_vals), max(numeric_vals)
        avg = sum(numeric_vals) / len(numeric_vals)

        if chart_type in ("line",):
            first, last = numeric_vals[0], numeric_vals[-1]
            change_pct = ((last - first) / first * 100) if first else 0
            direction = "up" if last > first else ("down" if last < first else "flat")
            return (
                f"{y} trended {direction} {abs(change_pct):.1f}% over the period "
                f"(min {mn:,.2f}, max {mx:,.2f})."
            )
        elif chart_type == "bar":
            # Find the label at max
            max_label = next(
                (r.get(x) for r in records if str(r.get(y)) == str(mx) or
                 (lambda v: float(v) == mx if v is not None else False)(r.get(y))),
                "unknown"
            )
            return f"Highest {y} is {mx:,.2f} (avg {avg:,.2f}, range {mn:,.2f}–{mx:,.2f})."
        elif chart_type == "scatter":
            return f"{y} ranges {mn:,.2f}–{mx:,.2f} across {len(numeric_vals)} points (avg {avg:,.2f})."
        elif chart_type == "pie":
            total = sum(numeric_vals) if numeric_vals else 1
            max_share = mx / total * 100
            return f"Largest slice is {max_share:.1f}% of the total ({len(records)} categories)."
        else:
            return f"{y}: min {mn:,.2f}, max {mx:,.2f}, avg {avg:,.2f} across {len(records)} rows."
    except Exception:
        return f"Chart of {y} vs {x} ({len(records)} rows)."


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

registry.register(
    name="read_local_data",
    toolset="data_viz",
    schema={
        "name": "read_local_data",
        "description": (
            "Read a local data file (CSV, JSON, TSV, Excel) from disk and return "
            "its schema, a 5-row preview, row count, and full records as JSON. "
            "Use this before calling make_card so you know the exact column names."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~ path to the data file (e.g. ~/Desktop/sales.csv)",
                },
            },
            "required": ["path"],
        },
    },
    handler=lambda args, **kw: read_local_data(
        path=args.get("path", ""),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="fetch_web_data",
    toolset="data_viz",
    schema={
        "name": "fetch_web_data",
        "description": (
            "Fetch a JSON API or CSV file from a URL and return its schema, "
            "a 5-row preview, row count, and full records as JSON. "
            "Use this before calling make_card so you know the exact column names."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full https:// URL to the JSON or CSV endpoint",
                },
            },
            "required": ["url"],
        },
    },
    handler=lambda args, **kw: fetch_web_data(
        url=args.get("url", ""),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="make_card",
    toolset="data_viz",
    schema={
        "name": "make_card",
        "description": (
            "Render an interactive HTML knowledge card (Plotly chart) from data records "
            "and save it to the cards directory. Always inspect the schema from "
            "read_local_data / fetch_web_data first to confirm column names. "
            "Returns the saved file path and a plain-English takeaway."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "records": {
                    "type": "string",
                    "description": (
                        "JSON string — array of objects, one per data row. "
                        "Pass the 'records' field from read_local_data or fetch_web_data."
                    ),
                },
                "chart_type": {
                    "type": "string",
                    "enum": ["line", "bar", "scatter", "pie"],
                    "description": "Chart type: line (trend), bar (compare), scatter (relationship), pie (parts of whole)",
                },
                "x": {
                    "type": "string",
                    "description": "Column name for the X axis (or labels for pie).",
                },
                "y": {
                    "type": "string",
                    "description": (
                        "Column name(s) for Y values. "
                        "For multiple series, comma-separate: 'revenue,cost'. "
                        "For pie, this is the values column."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Chart title shown at the top of the card.",
                },
                "source": {
                    "type": "string",
                    "description": "Citation string — file path or URL; shown at bottom of card.",
                },
                "cards_dir": {
                    "type": "string",
                    "description": (
                        "Directory to save the HTML card. "
                        f"Defaults to {_DEFAULT_CARDS_DIR!r}."
                    ),
                },
            },
            "required": ["records", "chart_type", "x", "y", "title", "source"],
        },
    },
    handler=lambda args, **kw: make_card(
        records=args.get("records", "[]"),
        chart_type=args.get("chart_type", "bar"),
        x=args.get("x", ""),
        y=args.get("y", ""),
        title=args.get("title", ""),
        source=args.get("source", ""),
        cards_dir=args.get("cards_dir", _DEFAULT_CARDS_DIR),
        task_id=kw.get("task_id"),
    ),
)
