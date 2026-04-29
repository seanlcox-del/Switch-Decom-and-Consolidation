# Switch Decom Dashboard

Self-contained HTML dashboard for tracking the Verizon switch decommission program (non-SWIFT COs).

## Components

| File | Purpose |
|------|---------|
| `generate_switch_decom_html.py` | Queries Oracle NARPROD, builds `switch_decom_dashboard.html` |
| `switch_decom_server.py` | Flask server (localhost:5000) for live notes write-back and circuit drill-down |

## Setup

1. **Install dependencies**
   ```bash
   pip install oracledb flask pandas numpy python-dotenv
   ```

2. **Create `.env` file** (never commit this)
   ```
   ORACLE_DSN=<host>:<port>/<service>
   ORACLE_USER=<username>
   ORACLE_PASS=<password>
   ```

3. **Generate dashboard**
   ```bash
   python generate_switch_decom_html.py
   ```

4. **Start Flask server** (for live notes + circuit drill-down)
   ```bash
   python switch_decom_server.py
   ```
   Then open `switch_decom_dashboard.html` in your browser.

## Dashboard Tabs

1. **Summary** — KPI cards, state/region/wave charts
2. **Switch Inventory** — filterable switch table with TIRKS badges
3. **Circuit Status** — USOC-based type breakdown, status overview
4. **Wave Analysis** — circuits and sites by wave
5. **Device Inventory** — TIRKS physical equipment (CLLIs with TIRKS data only)
6. **Velocity & Risk** — monthly migration velocity + cutover risk scoring
7. **Program KPIs** — savings estimates, site activity timeline
8. **Flagged Sites** — live from Flask server; notes and flags per CLLI
9. **Site Lookup** — per-CLLI circuit drill-down (live from Flask)

## Data Sources

| Table | Schema | Description |
|-------|--------|-------------|
| `BT_NT_RETIREMENT` | `GPSAA` | 203 non-SWIFT switches |
| `NT_DECOM_CIRCUITS_SOURCE` | `VNADSPRD` | ~893K circuits across 2,094 CLLIs |
| `SWIFT_FIRST_200_G5S` | `GPSAA` | 100 SWIFT CLLIs (excluded) |
| `TIRKS_DYCS_DEVICE_SUMMARY_VE` | `DECOM` | Physical device inventory |

## Notes

- Oracle credentials must be in `.env` — see Setup above
- `Plotly.js` (~4.3 MB) is embedded inline in the generated HTML
- The generated HTML file (~12.7 MB) is excluded from git via `.gitignore`
