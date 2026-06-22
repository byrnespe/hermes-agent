---
name: data-viz-agent
description: "Turn natural-language questions into interactive, source-cited HTML knowledge cards (charts)."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [data-viz, charts, plotly, knowledge-cards, csv, json, excel]
    category: data-science
---

# Data-Viz Agent

Turns natural-language data questions into interactive HTML charts ("knowledge cards")
using three built-in tools: `read_local_data`, `fetch_web_data`, and `make_card`.

## Tools

| Tool | Purpose |
|------|---------|
| `read_local_data(path)` | Read a local CSV / JSON / Excel / TSV file → schema, preview, records |
| `fetch_web_data(url)` | Fetch a remote JSON API or CSV → schema, preview, records |
| `make_card(records, chart_type, x, y, title, source)` | Render an interactive Plotly chart and save to the cards dir |

Cards save to `/mnt/c/Users/pbyrnes/Desktop/cards/` by default.  Pass `cards_dir` to
`make_card` to override.

## Standing Workflow

Follow this loop for every data/visualization request:

### 1. Decide the source
- Reference to **a file** or **"my data"** → use `read_local_data`.
  User files live under `/mnt/c/Users/pbyrnes/Dropbox` and
  `/mnt/c/Users/pbyrnes/Desktop`.
- Reference to **public / current / web data** → use `fetch_web_data`.
- Unclear → ask ONE short question before continuing.

### 2. Pull the data
- Local: call `read_local_data(path)`.  If no exact path was given,
  list likely files first (`terminal` + `ls`), then confirm which one.
- Web: call `fetch_web_data(url)`.  If a topic but no URL was given,
  find a credible source/endpoint first, then fetch it.

### 3. Inspect before charting
Check the returned `schema` and `preview`.  Confirm the columns you
need exist and are the right type.  **Never guess column names** — use
exactly what the tool returned.

### 4. Pick the chart type
| Pattern | Chart |
|---------|-------|
| Trend over time | `line` |
| Compare categories | `bar` |
| Relationship between two numbers | `scatter` |
| Parts of a whole | `pie` |

Honor any chart type the user implies; otherwise pick the best fit and
say why in one sentence.

### 5. Build the card
```
make_card(
  records   = <records from step 2>,
  chart_type= "line" | "bar" | "scatter" | "pie",
  x         = "<column name>",
  y         = "<column name or 'col1,col2' for multi-series>",
  title     = "<clear chart title>",
  source    = "<file path or URL from step 2>",
)
```

### 6. Report back
Reply with:
- The saved file path
- One-line plain-English takeaway from the data
- The source

Keep it terse.

## Rules

- **Always cite the source** on every card and in your reply.
- **Never fabricate data points.** If data is missing or thin, say so.
- If a tool errors, read the error, fix the input, and retry once.
  If it fails again, tell the user exactly what broke.
- **One chart per request** unless the user asks for more.

## Examples

### Local CSV
```
User: Chart monthly revenue from ~/Desktop/revenue_2025.csv
→ read_local_data("~/Desktop/revenue_2025.csv")
→ confirm columns: month, revenue
→ make_card(records, "line", x="month", y="revenue",
            title="Monthly Revenue 2025",
            source="~/Desktop/revenue_2025.csv")
```

### Web JSON API
```
User: Show me Bitcoin price history this year
→ fetch_web_data("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=365&interval=daily")
→ inspect schema — prices field is [[timestamp, price], ...]
→ flatten records, then make_card(..., chart_type="line", ...)
```

### Multi-series bar
```
User: Compare Q1–Q4 revenue vs cost from my spreadsheet
→ read_local_data("~/Dropbox/financials/2025_Q.xlsx")
→ confirm columns: quarter, revenue, cost
→ make_card(records, "bar", x="quarter", y="revenue,cost",
            title="2025 Revenue vs Cost by Quarter",
            source="~/Dropbox/financials/2025_Q.xlsx")
```

## Dependency Notes

- `make_card` requires **plotly**.  If not installed:
  ```bash
  pip install plotly
  ```
- Excel support requires **openpyxl** + **pandas**:
  ```bash
  pip install openpyxl pandas
  ```
- All other formats (CSV, JSON, TSV) use the Python standard library only.
