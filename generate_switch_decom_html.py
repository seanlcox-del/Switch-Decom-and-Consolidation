#!/usr/bin/env python3
"""
Switch Decommission Dashboard Generator (Non-SWIFT COs only)
Queries: GPSAA.BT_NT_RETIREMENT + VNADSPRD.NT_DECOM_CIRCUITS_SOURCE
Excludes: GPSAA.SWIFT_FIRST_200_G5S
Output:   Desktop/switch_decom_dashboard.html
"""
import json, warnings, os
from pathlib import Path
from datetime import date
import pandas as pd
import numpy as np
import oracledb
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

# Load credentials from .env (never commit .env to git)
load_dotenv(Path(__file__).parent / ".env")

DESKTOP     = Path(r"C:\Users\v296938\Desktop")
OUTPUT      = DESKTOP / "switch_decom_dashboard.html"
PLOTLY_FILE = DESKTOP / "SQDB OFS SITE FORECAST" / "plotly-2.35.2.min.js"
DSN  = os.environ["ORACLE_DSN"]
USER = os.environ["ORACLE_USER"]
PASS = os.environ["ORACLE_PASS"]
TODAY = date.today().strftime("%B %d, %Y")

# ─── Connect ──────────────────────────────────────────────────────────────────
print("Connecting to Oracle NARPROD...")
conn = oracledb.connect(user=USER, password=PASS, dsn=DSN)

# ─── SWIFT exclusion list ────────────────────────────────────────────────────
print("Loading SWIFT exclusion list...")
swift_df = pd.read_sql("SELECT WC_CLLI FROM GPSAA.SWIFT_FIRST_200_G5S", conn)
swift_set = set(swift_df['WC_CLLI'].str.upper().str.strip().tolist())
swift_sql = ",".join(f"'{c}'" for c in swift_set)
print(f"  {len(swift_set)} SWIFT CLLIs excluded")

# ─── Switch inventory ─────────────────────────────────────────────────────────
print("Loading switch inventory (BT_NT_RETIREMENT)...")
switch_df = pd.read_sql(f"""
    SELECT CLLI, WIRE_CENTER, STATE, REGION, SWITCH_TYPE, SWITCH_STATUS,
           SWITCH_CUTOVER_DATE, SWITCH_POWER_DOWN_DATE, CUTOVER_YEAR,
           ANNUAL_POWER_SAVINGS, TOTAL_ACTUAL_SAVINGS_PRTXDIS,
           COPPER_FOOTAGE, ANALOG_OE, PG__UNIVERSAL, PG__INTEGRATED, OLTFTTP
    FROM GPSAA.BT_NT_RETIREMENT
    WHERE UPPER(CLLI) NOT IN ({swift_sql})
""", conn)
switch_df['CLLI'] = switch_df['CLLI'].str.upper().str.strip()
# Clean numeric-ish savings columns
for col in ['ANNUAL_POWER_SAVINGS','TOTAL_ACTUAL_SAVINGS_PRTXDIS','COPPER_FOOTAGE']:
    switch_df[col] = pd.to_numeric(switch_df[col], errors='coerce')
print(f"  {len(switch_df):,} non-SWIFT switches")

# ─── Circuit aggregations ─────────────────────────────────────────────────────
print("Loading circuit data (may take ~60s)...")
circuit_df = pd.read_sql(f"""
    SELECT
        CLLI_CD,
        MAX(WIRE_CENTER_NAME)   AS WC_NAME,
        MAX(WIRE_CENTER_REGION) AS REGION,
        MAX(WIRE_CENTER_STATE)  AS STATE,
        COUNT(*)                AS TOTAL,
        SUM(CASE WHEN MIGRATION_COMPLETE='Y' THEN 1 ELSE 0 END) AS COMPLETED,
        SUM(CASE WHEN MIGRATION_COMPLETE='P' THEN 1 ELSE 0 END) AS IN_PROGRESS,
        SUM(CASE WHEN COPPER_FIBER_IND='C'   THEN 1 ELSE 0 END) AS COPPER,
        SUM(CASE WHEN COPPER_FIBER_IND='F'   THEN 1 ELSE 0 END) AS FIBER,
        MIN(DECOM_WAVE_ID)      AS MIN_WAVE,
        MAX(DECOM_WAVE_ID)      AS MAX_WAVE
    FROM VNADSPRD.NT_DECOM_CIRCUITS_SOURCE
    WHERE CLLI_CD IS NOT NULL
    AND UPPER(CLLI_CD) NOT IN ({swift_sql})
    GROUP BY CLLI_CD
""", conn)
circuit_df['CLLI_CD'] = circuit_df['CLLI_CD'].str.upper().str.strip()
total_ckts = int(circuit_df['TOTAL'].sum())
total_done = int(circuit_df['COMPLETED'].sum())
total_prog = int(circuit_df['IN_PROGRESS'].sum())
total_pend = total_ckts - total_done - total_prog
pct_done   = round(total_done / total_ckts * 100, 1) if total_ckts else 0
print(f"  {len(circuit_df):,} CLLIs | {total_ckts:,} circuits | {pct_done}% complete")

# ─── Wave summary ─────────────────────────────────────────────────────────────
print("Wave summary...")
wave_df = pd.read_sql(f"""
    SELECT DECOM_WAVE_ID AS WAVE, COUNT(*) AS TOTAL,
           SUM(CASE WHEN MIGRATION_COMPLETE='Y' THEN 1 ELSE 0 END) AS COMPLETED
    FROM VNADSPRD.NT_DECOM_CIRCUITS_SOURCE
    WHERE DECOM_WAVE_ID IS NOT NULL
    AND UPPER(CLLI_CD) NOT IN ({swift_sql})
    GROUP BY DECOM_WAVE_ID ORDER BY DECOM_WAVE_ID
""", conn)

# ─── Segment summary ──────────────────────────────────────────────────────────
print("Segment summary...")
seg_df = pd.read_sql(f"""
    SELECT NVL(SALES_SEGMENT_NAME,'Unknown') AS SEGMENT,
           COUNT(*) AS TOTAL,
           SUM(CASE WHEN MIGRATION_COMPLETE='Y' THEN 1 ELSE 0 END) AS COMPLETED
    FROM VNADSPRD.NT_DECOM_CIRCUITS_SOURCE
    WHERE UPPER(CLLI_CD) NOT IN ({swift_sql})
    GROUP BY SALES_SEGMENT_NAME ORDER BY TOTAL DESC
""", conn)

# ─── Circuit type breakdown (by BILLING_USOC_DESC category) ──────────────────
print("Circuit type breakdown...")
_case = """
    CASE
        WHEN UPPER(BILLING_USOC_DESC) LIKE '%DS1%'
          OR UPPER(BILLING_USOC_DESC) LIKE '%1.544%'
          OR UPPER(BILLING_USOC_DESC) LIKE '%DS-1%'      THEN 'T1 / DS1'
        WHEN UPPER(BILLING_USOC_DESC) LIKE '%DS3%'
          OR UPPER(BILLING_USOC_DESC) LIKE '%44.736%'    THEN 'DS3 / T3'
        WHEN UPPER(BILLING_USOC_DESC) LIKE '%ISDN%'
          OR UPPER(BILLING_USOC_DESC) LIKE '%BRI%'
          OR UPPER(BILLING_USOC_DESC) LIKE '%PRI%'       THEN 'ISDN'
        WHEN UPPER(BILLING_USOC_DESC) LIKE '%CENTREX%'
          OR UPPER(BILLING_USOC_DESC) LIKE '% CTX %'
          OR UPPER(BILLING_USOC_DESC) LIKE 'CTX %'
          OR UPPER(BILLING_USOC_DESC) LIKE '%CNTRX%'     THEN 'Centrex'
        WHEN UPPER(BILLING_USOC_DESC) LIKE '%CUSTOPAK%'
          OR UPPER(BILLING_USOC_DESC) LIKE '%CUST OPAK%' THEN 'Custopak'
        WHEN UPPER(BILLING_USOC_DESC) LIKE '%DSL%'
          OR UPPER(BILLING_USOC_DESC) LIKE '%ADSL%'      THEN 'DSL'
        WHEN BILLING_USOC_DESC IS NULL
          OR UPPER(BILLING_USOC_DESC) LIKE '%UNAVAILABLE%'
          OR UPPER(BILLING_USOC_DESC) LIKE '%NO INFO%'   THEN 'Unknown'
        ELSE 'Other'
    END
"""
cktype_df = pd.read_sql(f"""
    SELECT CKT_TYPE, COUNT(*) AS TOTAL,
           SUM(CASE WHEN MIGRATION_COMPLETE='Y' THEN 1 ELSE 0 END) AS COMPLETED
    FROM (
        SELECT MIGRATION_COMPLETE, {_case} AS CKT_TYPE
        FROM VNADSPRD.NT_DECOM_CIRCUITS_SOURCE
        WHERE UPPER(CLLI_CD) NOT IN ({swift_sql})
    ) t
    GROUP BY CKT_TYPE
    ORDER BY TOTAL DESC
""", conn)
cktype_col = "BILLING_USOC_DESC category"
print(f"  {len(cktype_df)} circuit categories")

# ─── Migration velocity (monthly completions) ────────────────────────────────
print("Migration velocity...")
velocity_df = pd.read_sql(f"""
    SELECT TO_CHAR(MIGRATION_COMPLETE_DATE,'YYYY-MM') AS YM,
           COUNT(*)                  AS COMPLETIONS,
           COUNT(DISTINCT CLLI_CD)   AS ACTIVE_SITES
    FROM VNADSPRD.NT_DECOM_CIRCUITS_SOURCE
    WHERE MIGRATION_COMPLETE_DATE IS NOT NULL
      AND MIGRATION_COMPLETE='Y'
      AND TO_CHAR(MIGRATION_COMPLETE_DATE,'YYYY') != '9999'
      AND UPPER(CLLI_CD) NOT IN ({swift_sql})
    GROUP BY TO_CHAR(MIGRATION_COMPLETE_DATE,'YYYY-MM')
    ORDER BY YM
""", conn)
velocity_df['COMPLETIONS']  = pd.to_numeric(velocity_df['COMPLETIONS'],  errors='coerce').fillna(0).astype(int)
velocity_df['ACTIVE_SITES'] = pd.to_numeric(velocity_df['ACTIVE_SITES'], errors='coerce').fillna(0).astype(int)
# 3-month rolling avg on last 3 months with data
last3 = velocity_df.tail(3)['COMPLETIONS'].tolist()
avg3  = round(sum(last3) / len(last3)) if last3 else 0
print(f"  {len(velocity_df)} months | last-3-mo avg: {avg3:,}/mo | active sites Jan-26: {velocity_df.iloc[-1]['ACTIVE_SITES']:,}")

# ─── Savings / asset summary ──────────────────────────────────────────────────
for col in ['ANNUAL_POWER_SAVINGS','TOTAL_ACTUAL_SAVINGS_PRTXDIS','COPPER_FOOTAGE']:
    switch_df[col] = pd.to_numeric(switch_df[col], errors='coerce')
switch_df['ANALOG_OE'] = pd.to_numeric(switch_df['ANALOG_OE'], errors='coerce')

sav_df = switch_df[switch_df['ANNUAL_POWER_SAVINGS'].notna()].copy()
total_annual_savings  = round(float(sav_df['ANNUAL_POWER_SAVINGS'].sum()), 2)
total_actual_savings  = round(float(switch_df['TOTAL_ACTUAL_SAVINGS_PRTXDIS'].dropna().sum()), 2)
total_copper_footage  = int(switch_df['COPPER_FOOTAGE'].fillna(0).sum())
total_analog_oe       = int(switch_df['ANALOG_OE'].fillna(0).sum())
savings_populated     = int(switch_df['ANNUAL_POWER_SAVINGS'].notna().sum())

# Savings by switch status
sav_by_status = sav_df.groupby('SWITCH_STATUS').agg(
    count=('CLLI','count'),
    annual=('ANNUAL_POWER_SAVINGS','sum')
).reset_index().sort_values('annual', ascending=False)

# Copper footage + analog OE by state
assets_by_state = switch_df.groupby('STATE', dropna=False).agg(
    copper=('COPPER_FOOTAGE','sum'),
    analog_oe=('ANALOG_OE','sum')
).reset_index().fillna(0).sort_values('copper', ascending=False)
assets_by_state['STATE'] = assets_by_state['STATE'].fillna('Unknown')

print(f"  Annual power savings: ${total_annual_savings:,.0f} ({savings_populated} switches) | Actual: ${total_actual_savings:,.0f}")
print(f"  Copper footage: {total_copper_footage:,} ft | Analog OE: {total_analog_oe:,}")

# ─── Cutover risk ──────────────────────────────────────────────────────────────
print("Cutover risk...")
risk_df = pd.read_sql(f"""
    SELECT s.CLLI, s.WIRE_CENTER, s.STATE, s.REGION,
           s.SWITCH_CUTOVER_DATE, s.CUTOVER_YEAR, s.SWITCH_STATUS, s.SWITCH_TYPE,
           NVL(c.TOTAL,0)     AS TOTAL,
           NVL(c.COMPLETED,0) AS COMPLETED,
           NVL(c.TOTAL,0) - NVL(c.COMPLETED,0) AS REMAINING
    FROM GPSAA.BT_NT_RETIREMENT s
    LEFT JOIN (
        SELECT CLLI_CD,
               COUNT(*) AS TOTAL,
               SUM(CASE WHEN MIGRATION_COMPLETE='Y' THEN 1 ELSE 0 END) AS COMPLETED
        FROM VNADSPRD.NT_DECOM_CIRCUITS_SOURCE
        WHERE UPPER(CLLI_CD) NOT IN ({swift_sql})
        GROUP BY CLLI_CD
    ) c ON UPPER(s.CLLI) = UPPER(c.CLLI_CD)
    WHERE UPPER(s.CLLI) NOT IN ({swift_sql})
    ORDER BY REMAINING DESC NULLS LAST
""", conn)
for col in ['TOTAL','COMPLETED','REMAINING']:
    risk_df[col] = pd.to_numeric(risk_df[col], errors='coerce').fillna(0).astype(int)
risk_df['PCT_DONE'] = (risk_df['COMPLETED'] / risk_df['TOTAL'].replace(0, np.nan) * 100).round(1).fillna(0)

def classify_risk(row):
    if row['REMAINING'] == 0:  return 'Complete'
    cd = row['SWITCH_CUTOVER_DATE']
    cy = row['CUTOVER_YEAR']
    has_date = (pd.notna(cd) and str(cd).strip() != '') or \
               (pd.notna(cy) and str(cy).strip() != '')
    if not has_date:           return 'No Date Set'
    if row['PCT_DONE'] < 25:   return 'High Risk'
    if row['PCT_DONE'] < 75:   return 'Medium Risk'
    return 'Lower Risk'

risk_df['RISK'] = risk_df.apply(classify_risk, axis=1)
print(f"  {len(risk_df)} switches | risk breakdown: {risk_df['RISK'].value_counts().to_dict()}")

# velocity summary stats
vel_total_completed = int(velocity_df['COMPLETIONS'].sum())
vel_last_month      = int(velocity_df.iloc[-1]['COMPLETIONS']) if len(velocity_df) else 0
vel_last_month_ym   = str(velocity_df.iloc[-1]['YM']) if len(velocity_df) else ''
months_to_complete  = round((total_ckts - total_done) / avg3) if avg3 else None

# ─── Device inventory (TIRKS_DYCS_DEVICE_SUMMARY_VE) ─────────────────────────
print("Loading device inventory from TIRKS...")
device_df = pd.read_sql(f"""
    SELECT d.BUILDING_CLLI,
           d.DEVICE_NAME,
           d.DEVICE_TYPE,
           d.CKT_COUNT,
           d.DEVICE_UTILIZATION,
           d.DEVICE_POWERED_DOWN,
           d.DEVICE_REMOVED,
           d.CURRENT_MILESTONE,
           d.CURRENT_MILESTONE_STATUS,
           TO_CHAR(d.CURRENT_MILESTONE_START_DT,'YYYY-MM-DD') AS MILESTONE_START,
           TO_CHAR(d.CURRENT_MILESTONE_END_DT,'YYYY-MM-DD')   AS MILESTONE_END,
           d.POWER_SAVINGS
    FROM DECOM.TIRKS_DYCS_DEVICE_SUMMARY_VE d
    WHERE d.BUILDING_CLLI IN (
        SELECT DISTINCT UPPER(CLLI_CD)
        FROM VNADSPRD.NT_DECOM_CIRCUITS_SOURCE
        WHERE CLLI_CD IS NOT NULL
        AND UPPER(CLLI_CD) NOT IN ({swift_sql})
    )
    ORDER BY d.BUILDING_CLLI, d.CKT_COUNT DESC NULLS LAST
""", conn)
device_df['BUILDING_CLLI'] = device_df['BUILDING_CLLI'].str.upper().str.strip()
for col in ['CKT_COUNT','POWER_SAVINGS']:
    device_df[col] = pd.to_numeric(device_df[col], errors='coerce').fillna(0)
matched_dev_cllis = device_df['BUILDING_CLLI'].nunique()
total_devices     = len(device_df)
total_pwrdown     = int((device_df['DEVICE_POWERED_DOWN'] == 'Y').sum())
total_pwr_savings = int(device_df['POWER_SAVINGS'].sum())
print(f"  {total_devices:,} device records across {matched_dev_cllis:,} CLLIs | {total_pwrdown:,} powered down")

# ─── Switch dependency (BAAIS_SWITCH_DEPENDENCY) ─────────────────────────────
print("Loading switch dependency data...")
dep_raw = pd.read_sql(
    "SELECT SWITCH_CLLI, WIRE_CENTER_CLLI FROM VNADSPRD.BAAIS_SWITCH_DEPENDENCY",
    conn
)
dep_raw['SWITCH_CLLI']      = dep_raw['SWITCH_CLLI'].str.upper().str.strip()
dep_raw['WIRE_CENTER_CLLI'] = dep_raw['WIRE_CENTER_CLLI'].str.upper().str.strip()

decom_cllis = set(switch_df['CLLI'].str.upper())

# Upstream: for each decom CLLI, which switch(es) does it route through?
upstream_map = (
    dep_raw[dep_raw['WIRE_CENTER_CLLI'].isin(decom_cllis)]
    .groupby('WIRE_CENTER_CLLI')['SWITCH_CLLI']
    .apply(list).to_dict()
)
# Dependents: for each decom CLLI acting as a parent switch, which WCs depend on it?
dep_map = (
    dep_raw[dep_raw['SWITCH_CLLI'].isin(decom_cllis)]
    .groupby('SWITCH_CLLI')['WIRE_CENTER_CLLI']
    .apply(list).to_dict()
)

dep_rows = []
for clli in sorted(decom_cllis):
    up   = upstream_map.get(clli, [])
    deps = dep_map.get(clli, [])
    dep_rows.append({
        'CLLI':           clli,
        'UPSTREAM':       ', '.join(sorted(set(up))),
        'UPSTREAM_COUNT': len(set(up)),
        'DEP_COUNT':      len(deps),
        'DEP_LIST':       ', '.join(sorted(set(deps))),
    })
dep_df = pd.DataFrame(dep_rows)

# Enrich with switch state/region/pct done from merged/risk
dep_df = dep_df.merge(
    risk_df[['CLLI','STATE','REGION','SWITCH_TYPE','PCT_DONE','RISK']],
    on='CLLI', how='left'
)

dep_switches_with_deps = int((dep_df['DEP_COUNT'] > 0).sum())
dep_total_wcs          = int(dep_df['DEP_COUNT'].sum())
dep_max_deps           = int(dep_df['DEP_COUNT'].max())
print(f"  {dep_switches_with_deps} decom switches have dependents | max={dep_max_deps} | total dep WCs={dep_total_wcs}")

conn.close()
print("Oracle queries complete.")

# ─── Merge circuit + switch ────────────────────────────────────────────────────
merged = circuit_df.merge(
    switch_df[['CLLI','SWITCH_TYPE','SWITCH_STATUS','SWITCH_CUTOVER_DATE',
               'CUTOVER_YEAR','ANNUAL_POWER_SAVINGS','COPPER_FOOTAGE']],
    left_on='CLLI_CD', right_on='CLLI', how='left'
).drop(columns=['CLLI'])

# Region rollup from merged
region_df = merged.groupby('REGION', dropna=False).agg(
    cllis=('CLLI_CD','count'),
    total=('TOTAL','sum'),
    completed=('COMPLETED','sum'),
    in_progress=('IN_PROGRESS','sum'),
    copper=('COPPER','sum'),
    fiber=('FIBER','sum')
).reset_index().sort_values('total', ascending=False)
region_df['REGION'] = region_df['REGION'].fillna('Unknown')

# ─── JSON helpers ─────────────────────────────────────────────────────────────
def clean(v):
    if v is None: return None
    if isinstance(v, float) and np.isnan(v): return None
    if hasattr(v, 'item'): return v.item()
    return v

def df_to_cols(df, cols):
    out = {}
    for c in cols:
        out[c] = [clean(v) for v in df[c].tolist()] if c in df.columns else []
    return out

# ─── Assemble data ────────────────────────────────────────────────────────────
print("Serializing...")
DATA = {
    "summary": {
        "total_cllis":    len(circuit_df),
        "total_switches": len(switch_df),
        "total_circuits": total_ckts,
        "completed":      total_done,
        "in_progress":    total_prog,
        "pending":        total_pend,
        "pct_complete":   pct_done,
        "swift_excluded": len(swift_set),
        "as_of":          TODAY,
    },
    "circuits": df_to_cols(
        merged.fillna("").sort_values("TOTAL", ascending=False),
        ["CLLI_CD","WC_NAME","REGION","STATE","TOTAL","COMPLETED","IN_PROGRESS",
         "COPPER","FIBER","MIN_WAVE","MAX_WAVE","SWITCH_TYPE","SWITCH_STATUS",
         "SWITCH_CUTOVER_DATE","CUTOVER_YEAR"]
    ),
    "switches": df_to_cols(
        switch_df.fillna("").sort_values("CLLI"),
        ["CLLI","WIRE_CENTER","STATE","REGION","SWITCH_TYPE","SWITCH_STATUS",
         "SWITCH_CUTOVER_DATE","CUTOVER_YEAR","ANNUAL_POWER_SAVINGS",
         "TOTAL_ACTUAL_SAVINGS_PRTXDIS","COPPER_FOOTAGE",
         "ANALOG_OE","PG__UNIVERSAL","PG__INTEGRATED","OLTFTTP"]
    ),
    "regions": df_to_cols(region_df, ["REGION","cllis","total","completed","in_progress","copper","fiber"]),
    "waves":   df_to_cols(wave_df,   ["WAVE","TOTAL","COMPLETED"]),
    "segments":df_to_cols(seg_df,    ["SEGMENT","TOTAL","COMPLETED"]),
    "cktypes": df_to_cols(cktype_df, ["CKT_TYPE","TOTAL","COMPLETED"]),
    "devices": df_to_cols(
        device_df.fillna(""),
        ["BUILDING_CLLI","DEVICE_NAME","DEVICE_TYPE","CKT_COUNT","DEVICE_UTILIZATION",
         "DEVICE_POWERED_DOWN","DEVICE_REMOVED","CURRENT_MILESTONE",
         "CURRENT_MILESTONE_STATUS","MILESTONE_START","MILESTONE_END","POWER_SAVINGS"]
    ),
    "velocity": df_to_cols(velocity_df, ["YM","COMPLETIONS","ACTIVE_SITES"]),
    "vel_summary": {
        "avg3":               avg3,
        "last_month":         vel_last_month,
        "last_month_ym":      vel_last_month_ym,
        "months_to_complete": months_to_complete,
        "total_completed":    vel_total_completed,
    },
    "savings": {
        "total_annual":       total_annual_savings,
        "total_actual":       total_actual_savings,
        "copper_footage":     total_copper_footage,
        "analog_oe":          total_analog_oe,
        "savings_populated":  savings_populated,
        "total_switches":     len(switch_df),
        "by_status_labels":   sav_by_status['SWITCH_STATUS'].tolist(),
        "by_status_annual":   [round(float(v),2) for v in sav_by_status['annual'].tolist()],
        "by_status_count":    sav_by_status['count'].tolist(),
        "state_labels":       assets_by_state['STATE'].tolist(),
        "state_copper":       [int(v) for v in assets_by_state['copper'].tolist()],
        "state_analog":       [int(v) for v in assets_by_state['analog_oe'].tolist()],
    },
    "risk": df_to_cols(
        risk_df.fillna(""),
        ["CLLI","WIRE_CENTER","STATE","REGION","SWITCH_TYPE","SWITCH_STATUS",
         "SWITCH_CUTOVER_DATE","CUTOVER_YEAR","TOTAL","COMPLETED","REMAINING","PCT_DONE","RISK"]
    ),
    "dev_summary": {
        "matched_cllis":   matched_dev_cllis,
        "total_devices":   total_devices,
        "powered_down":    total_pwrdown,
        "power_savings_w": total_pwr_savings,
    },
    "deps": df_to_cols(
        dep_df.fillna("").sort_values("DEP_COUNT", ascending=False),
        ["CLLI","STATE","REGION","SWITCH_TYPE","PCT_DONE","RISK",
         "UPSTREAM","UPSTREAM_COUNT","DEP_COUNT","DEP_LIST"]
    ),
    "dep_summary": {
        "switches_with_deps": dep_switches_with_deps,
        "total_dep_wcs":      dep_total_wcs,
        "max_deps":           dep_max_deps,
    },
}

data_json = json.dumps(DATA, default=str)
print(f"Data size: {len(data_json)/1e6:.2f} MB")

# ─── Plotly ───────────────────────────────────────────────────────────────────
print("Loading Plotly.js...")
plotly_js = PLOTLY_FILE.read_text(encoding='utf-8')

# ─── HTML ─────────────────────────────────────────────────────────────────────
print("Generating HTML...")

html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Switch Decommission Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;background:#f0f2f5;color:#212529}
.hdr{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 60%,#0f3460 100%);color:#fff;padding:18px 28px}
.hdr h1{font-size:1.5rem;font-weight:700;letter-spacing:.5px}
.hdr .sub{font-size:.82rem;color:#adb5bd;margin-top:4px}
.tabs{display:flex;background:#fff;border-bottom:2px solid #dee2e6;padding:0 20px;overflow-x:auto}
.tab-btn{padding:12px 20px;border:none;background:none;cursor:pointer;font-size:.88rem;font-weight:600;color:#6c757d;border-bottom:3px solid transparent;white-space:nowrap;transition:.2s}
.tab-btn.active{color:#0d6efd;border-bottom-color:#0d6efd}
.tab-btn:hover:not(.active){color:#495057;border-bottom-color:#dee2e6}
.tab-pane{display:none;padding:20px}
.tab-pane.active{display:block}
.kpi-row{display:grid;gap:14px;margin-bottom:18px}
.kpi-row-5{grid-template-columns:repeat(5,1fr)}
.kpi-row-4{grid-template-columns:repeat(4,1fr)}
.kpi-row-3{grid-template-columns:repeat(3,1fr)}
.kpi{background:#fff;border-radius:8px;padding:16px 18px;box-shadow:0 1px 4px rgba(0,0,0,.08);border-left:4px solid #dee2e6}
.kpi.c-blue{border-left-color:#0d6efd}.kpi.c-green{border-left-color:#198754}
.kpi.c-orange{border-left-color:#fd7e14}.kpi.c-purple{border-left-color:#6f42c1}
.kpi.c-red{border-left-color:#dc3545}.kpi.c-teal{border-left-color:#0dcaf0}
.kpi-label{font-size:.72rem;font-weight:600;text-transform:uppercase;color:#6c757d;letter-spacing:.5px}
.kpi-value{font-size:1.8rem;font-weight:700;color:#212529;margin:4px 0 2px}
.kpi-sub{font-size:.75rem;color:#868e96}
.card{background:#fff;border-radius:8px;padding:18px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:16px}
.card-title{font-size:.9rem;font-weight:700;color:#495057;margin-bottom:14px;text-transform:uppercase;letter-spacing:.4px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.filters label{font-size:.8rem;font-weight:600;color:#495057}
select,input[type=text]{padding:5px 10px;border:1px solid #dee2e6;border-radius:5px;font-size:.82rem;background:#fff}
select:focus,input:focus{outline:none;border-color:#0d6efd}
.tbl-wrap{overflow-x:auto;max-height:480px;overflow-y:auto}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th{background:#f8f9fa;position:sticky;top:0;z-index:1;padding:8px 10px;text-align:left;font-weight:700;color:#495057;border-bottom:2px solid #dee2e6;cursor:pointer;white-space:nowrap}
th:hover{background:#e9ecef}
td{padding:7px 10px;border-bottom:1px solid #f1f3f5;color:#212529;vertical-align:middle}
tr:hover td{background:#f8f9fa}
.pbar{background:#e9ecef;border-radius:4px;height:8px;min-width:80px}
.pbar-fill{background:#198754;border-radius:4px;height:8px;transition:.3s}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.7rem;font-weight:600}
.badge-remove{background:#d1e7dd;color:#0a3622}
.badge-aip{background:#fff3cd;color:#664d03}
.badge-y{background:#d1e7dd;color:#0a3622}
.badge-p{background:#fff3cd;color:#664d03}
.badge-n{background:#f8d7da;color:#58151c}
.lookup-box{max-width:520px;margin-bottom:20px}
.lookup-box input{width:100%;padding:10px 14px;font-size:1rem;border:2px solid #dee2e6;border-radius:8px}
.lookup-box input:focus{border-color:#0d6efd;outline:none}
.lookup-result{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.info-card{background:#fff;border-radius:8px;padding:18px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.info-card h3{font-size:.85rem;font-weight:700;text-transform:uppercase;color:#6c757d;margin-bottom:12px}
.info-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #f1f3f5;font-size:.82rem}
.info-row:last-child{border:none}
.info-label{color:#6c757d}
.info-val{font-weight:600;color:#212529;text-align:right}
#lookup-none{display:none;color:#fd7e14;font-size:.9rem;margin-top:10px;padding:10px;background:#fff3cd;border-radius:6px}
.notes-panel{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-top:12px}
.notes-panel h4{font-size:.78rem;font-weight:700;text-transform:uppercase;color:#6c757d;letter-spacing:.4px;margin-bottom:8px}
.notes-panel textarea{width:100%;min-height:90px;padding:8px 10px;border:1px solid #dee2e6;border-radius:6px;font-size:.83rem;font-family:inherit;resize:vertical}
.notes-panel textarea:focus{outline:none;border-color:#0d6efd}
.notes-actions{display:flex;align-items:center;gap:10px;margin-top:8px}
.btn{padding:6px 16px;border:none;border-radius:6px;font-size:.82rem;font-weight:600;cursor:pointer;transition:.15s}
.btn-primary{background:#0d6efd;color:#fff}.btn-primary:hover{background:#0b5ed7}
.btn-outline{background:#fff;color:#495057;border:1px solid #dee2e6}.btn-outline:hover{background:#f8f9fa}
.flag-toggle{display:flex;align-items:center;gap:6px;font-size:.82rem;font-weight:600;cursor:pointer;color:#6c757d}
.flag-toggle input{cursor:pointer;width:15px;height:15px}
.flag-toggle.flagged{color:#fd7e14}
.notes-saved{font-size:.78rem;color:#198754;margin-left:4px;display:none}
@media(max-width:900px){.kpi-row-5,.kpi-row-4{grid-template-columns:repeat(2,1fr)}.row2,.row3{grid-template-columns:1fr}}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<div class="hdr">
  <h1>&#128225; Switch Decommission Dashboard &mdash; Non-SWIFT Central Offices</h1>
  <div class="sub" id="hdr-sub">Loading...</div>
</div>

<div class="tabs">
  <button class="tab-btn active" onclick="showTab('summary',this)">Summary</button>
  <button class="tab-btn" onclick="showTab('switches',this)">Switch Inventory</button>
  <button class="tab-btn" onclick="showTab('circuits',this)">Circuit Status</button>
  <button class="tab-btn" onclick="showTab('waves',this)">Wave Analysis</button>
  <button class="tab-btn" onclick="showTab('devices',this)">Device Inventory</button>
  <button class="tab-btn" onclick="showTab('velocity',this)">Velocity &amp; Risk</button>
  <button class="tab-btn" onclick="showTab('kpis',this)">Program KPIs</button>
  <button class="tab-btn" onclick="showTab('flagged',this);loadFlaggedSites()">Flagged Sites</button>
  <button class="tab-btn" onclick="showTab('depmap',this);initDependencies()">Dependencies</button>
  <button class="tab-btn" onclick="showTab('lookup',this)">Site Lookup</button>
</div>

<!-- SUMMARY TAB -->
<div class="tab-pane active" id="tab-summary">
  <div class="kpi-row kpi-row-5">
    <div class="kpi c-blue"><div class="kpi-label">Total COs (CLLIs)</div><div class="kpi-value" id="kpi-cllis">—</div><div class="kpi-sub">with active circuits</div></div>
    <div class="kpi c-purple"><div class="kpi-label">Switch Records</div><div class="kpi-value" id="kpi-sw">—</div><div class="kpi-sub">in BT_NT_RETIREMENT</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Total Circuits</div><div class="kpi-value" id="kpi-tot">—</div><div class="kpi-sub">non-SWIFT COs</div></div>
    <div class="kpi c-green"><div class="kpi-label">Completed</div><div class="kpi-value" id="kpi-done">—</div><div class="kpi-sub" id="kpi-done-sub">—</div></div>
    <div class="kpi c-red"><div class="kpi-label">Remaining</div><div class="kpi-value" id="kpi-rem">—</div><div class="kpi-sub">not migrated</div></div>
  </div>
  <div class="row2">
    <div class="card"><div class="card-title">Circuit Migration Status</div><div id="chart-status-donut" style="height:300px"></div></div>
    <div class="card"><div class="card-title">Circuits by Region</div><div id="chart-region-bar" style="height:300px"></div></div>
  </div>
  <div class="row2">
    <div class="card"><div class="card-title">Copper vs Fiber Circuits</div><div id="chart-cf-donut" style="height:280px"></div></div>
    <div class="card"><div class="card-title">Circuits by Sales Segment</div><div id="chart-seg-bar" style="height:280px"></div></div>
  </div>
</div>

<!-- SWITCH INVENTORY TAB -->
<div class="tab-pane" id="tab-switches">
  <div class="lookup-box" style="max-width:400px;margin-bottom:16px">
    <label style="display:block;font-weight:700;margin-bottom:6px;font-size:.85rem">CLLI Lookup:</label>
    <input type="text" id="sw-lookup-input" placeholder="e.g. ENOLPAEN" oninput="doSwLookup()" style="text-transform:uppercase;width:100%;padding:8px 12px;font-size:.9rem;border:2px solid #dee2e6;border-radius:8px">
  </div>
  <div id="sw-lookup-result" style="display:none;margin-bottom:16px">
    <div class="info-card" style="max-width:600px">
      <h3 id="sw-lookup-title" style="font-size:.85rem;font-weight:700;text-transform:uppercase;color:#6c757d;margin-bottom:12px">Switch Detail</h3>
      <div id="sw-lookup-body"></div>
    </div>
  </div>
  <div id="sw-lookup-none" style="display:none;color:#fd7e14;font-size:.85rem;margin-bottom:16px;padding:10px;background:#fff3cd;border-radius:6px;max-width:400px">CLLI not found in switch inventory.</div>
  <div class="kpi-row kpi-row-4">
    <div class="kpi c-blue"><div class="kpi-label">Total Switches</div><div class="kpi-value" id="sw-kpi-tot">—</div><div class="kpi-sub">non-SWIFT COs</div></div>
    <div class="kpi c-green"><div class="kpi-label">Status: Remove</div><div class="kpi-value" id="sw-kpi-rem">—</div><div class="kpi-sub">fully decommed</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Status: AIP</div><div class="kpi-value" id="sw-kpi-aip">—</div><div class="kpi-sub">approval in progress</div></div>
    <div class="kpi c-purple"><div class="kpi-label">SWIFT Excluded</div><div class="kpi-value" id="sw-kpi-swift">—</div><div class="kpi-sub">filtered out</div></div>
  </div>
  <div class="row2">
    <div class="card"><div class="card-title">Switch Types</div><div id="chart-sw-type" style="height:300px"></div></div>
    <div class="card"><div class="card-title">Switches by State</div><div id="chart-sw-state" style="height:300px"></div></div>
  </div>
  <div class="card">
    <div class="card-title">Switch Inventory Detail</div>
    <div class="filters">
      <label>State:</label>
      <select id="sw-filter-state" onchange="renderSwTable()"><option value="">All</option></select>
      <label>Region:</label>
      <select id="sw-filter-region" onchange="renderSwTable()"><option value="">All</option></select>
      <label>Type:</label>
      <select id="sw-filter-type" onchange="renderSwTable()"><option value="">All</option></select>
      <label>Status:</label>
      <select id="sw-filter-status" onchange="renderSwTable()"><option value="">All</option></select>
      <label>TIRKS Data:</label>
      <select id="sw-filter-tirks" onchange="renderSwTable()">
        <option value="">All</option>
        <option value="Y">Has TIRKS</option>
        <option value="N">No TIRKS</option>
      </select>
    </div>
    <div class="tbl-wrap"><table id="sw-tbl">
      <thead><tr>
        <th onclick="sortTable('sw-tbl',0)">CLLI</th>
        <th onclick="sortTable('sw-tbl',1)">Wire Center</th>
        <th onclick="sortTable('sw-tbl',2)">State</th>
        <th onclick="sortTable('sw-tbl',3)">Region</th>
        <th onclick="sortTable('sw-tbl',4)">Switch Type</th>
        <th onclick="sortTable('sw-tbl',5)">Status</th>
        <th onclick="sortTable('sw-tbl',6)">Cutover Year</th>
        <th onclick="sortTable('sw-tbl',7)">Annual Savings ($)</th>
        <th onclick="sortTable('sw-tbl',8)">Copper Footage</th>
        <th onclick="sortTable('sw-tbl',9)">Analog OE</th>
        <th onclick="sortTable('sw-tbl',10)">TIRKS</th>
      </tr></thead>
      <tbody id="sw-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- CIRCUIT STATUS TAB -->
<div class="tab-pane" id="tab-circuits">
  <div class="filters">
    <label>Region:</label>
    <select id="ckt-filter-region" onchange="renderCktTable()"><option value="">All</option></select>
    <label>State:</label>
    <select id="ckt-filter-state" onchange="renderCktTable()"><option value="">All</option></select>
    <label>Switch Type:</label>
    <select id="ckt-filter-swtype" onchange="renderCktTable()"><option value="">All</option></select>
    <label>CLLI search:</label>
    <input type="text" id="ckt-search" placeholder="e.g. NYCM..." oninput="renderCktTable()" style="width:140px">
    <label>TIRKS Data:</label>
    <select id="ckt-filter-tirks" onchange="renderCktTable()">
      <option value="">All</option>
      <option value="Y">Has TIRKS</option>
      <option value="N">No TIRKS</option>
    </select>
  </div>
  <div class="kpi-row kpi-row-4" style="margin-bottom:14px">
    <div class="kpi c-blue"><div class="kpi-label">Filtered CLLIs</div><div class="kpi-value" id="ckt-kpi-cllis">—</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Filtered Circuits</div><div class="kpi-value" id="ckt-kpi-tot">—</div></div>
    <div class="kpi c-green"><div class="kpi-label">Completed</div><div class="kpi-value" id="ckt-kpi-done">—</div></div>
    <div class="kpi c-red"><div class="kpi-label">Remaining</div><div class="kpi-value" id="ckt-kpi-rem">—</div></div>
  </div>
  <div class="row2" id="cktype-charts" style="margin-bottom:16px">
    <div class="card"><div class="card-title" id="cktype-dist-title">Circuit Type Distribution</div><div id="chart-cktype-donut" style="height:320px"></div></div>
    <div class="card"><div class="card-title">Completion Rate by Circuit Type</div><div id="chart-cktype-bar" style="height:320px"></div></div>
  </div>
  <div class="card">
    <div class="card-title">Circuit Status by CLLI</div>
    <div class="tbl-wrap"><table id="ckt-tbl">
      <thead><tr>
        <th onclick="sortTable('ckt-tbl',0)">CLLI</th>
        <th onclick="sortTable('ckt-tbl',1)">Wire Center</th>
        <th onclick="sortTable('ckt-tbl',2)">Region</th>
        <th onclick="sortTable('ckt-tbl',3)">State</th>
        <th onclick="sortTable('ckt-tbl',4)">Total</th>
        <th onclick="sortTable('ckt-tbl',5)">Done</th>
        <th onclick="sortTable('ckt-tbl',6)">In Prog</th>
        <th onclick="sortTable('ckt-tbl',7)">Remaining</th>
        <th onclick="sortTable('ckt-tbl',8)">% Done</th>
        <th onclick="sortTable('ckt-tbl',9)">Switch Type</th>
        <th onclick="sortTable('ckt-tbl',10)">Wave(s)</th>
        <th onclick="sortTable('ckt-tbl',11)">TIRKS</th>
      </tr></thead>
      <tbody id="ckt-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- WAVE ANALYSIS TAB -->
<div class="tab-pane" id="tab-waves">
  <div class="row2">
    <div class="card"><div class="card-title">Circuits by Wave (Completed vs Remaining)</div><div id="chart-wave-bar" style="height:380px"></div></div>
    <div class="card"><div class="card-title">Completion Rate by Wave</div><div id="chart-wave-pct" style="height:380px"></div></div>
  </div>
  <div class="card">
    <div class="card-title">Wave Detail</div>
    <div class="tbl-wrap"><table id="wave-tbl">
      <thead><tr>
        <th onclick="sortTable('wave-tbl',0)">Wave</th>
        <th onclick="sortTable('wave-tbl',1)">Total Circuits</th>
        <th onclick="sortTable('wave-tbl',2)">Completed</th>
        <th onclick="sortTable('wave-tbl',3)">Remaining</th>
        <th onclick="sortTable('wave-tbl',4)">% Complete</th>
      </tr></thead>
      <tbody id="wave-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- DEVICE INVENTORY TAB -->
<div class="tab-pane" id="tab-devices">
  <div class="kpi-row kpi-row-4">
    <div class="kpi c-blue"><div class="kpi-label">Matched COs</div><div class="kpi-value" id="dev-kpi-cllis">—</div><div class="kpi-sub">CLLIs with TIRKS data</div></div>
    <div class="kpi c-purple"><div class="kpi-label">Total Devices</div><div class="kpi-value" id="dev-kpi-devs">—</div><div class="kpi-sub">network equipment records</div></div>
    <div class="kpi c-green"><div class="kpi-label">Powered Down</div><div class="kpi-value" id="dev-kpi-pwrdn">—</div><div class="kpi-sub">devices deenergized</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Est. Power Savings</div><div class="kpi-value" id="dev-kpi-pwr">—</div><div class="kpi-sub">watts (powered-down devs)</div></div>
  </div>
  <div class="row2">
    <div class="card"><div class="card-title">Device Types</div><div id="chart-dev-type" style="height:320px"></div></div>
    <div class="card"><div class="card-title">Device Utilization</div><div id="chart-dev-util" style="height:320px"></div></div>
  </div>
  <div class="row2">
    <div class="card"><div class="card-title">Milestone Status</div><div id="chart-dev-milestone" style="height:280px"></div></div>
    <div class="card"><div class="card-title">Top COs by Device Count</div><div id="chart-dev-clli" style="height:280px"></div></div>
  </div>
  <div class="card">
    <div class="card-title">Device Detail</div>
    <div class="filters">
      <label>CLLI:</label>
      <input type="text" id="dev-filter-clli" placeholder="e.g. NYCMNYWS" oninput="renderDevTable()" style="width:120px;text-transform:uppercase">
      <label>Device Type:</label>
      <select id="dev-filter-type" onchange="renderDevTable()"><option value="">All</option></select>
      <label>Utilization:</label>
      <select id="dev-filter-util" onchange="renderDevTable()"><option value="">All</option></select>
      <label>Powered Down:</label>
      <select id="dev-filter-pwrdn" onchange="renderDevTable()">
        <option value="">All</option>
        <option value="Y">Yes</option>
        <option value="N">No</option>
      </select>
      <label>Milestone:</label>
      <select id="dev-filter-milestone" onchange="renderDevTable()"><option value="">All</option></select>
      <span id="dev-tbl-count" style="font-size:.8rem;color:#6c757d;margin-left:auto"></span>
    </div>
    <div class="tbl-wrap"><table id="dev-tbl">
      <thead><tr>
        <th onclick="sortTable('dev-tbl',0)">CLLI</th>
        <th onclick="sortTable('dev-tbl',1)">Device Name</th>
        <th onclick="sortTable('dev-tbl',2)">Device Type</th>
        <th onclick="sortTable('dev-tbl',3)">Circuits</th>
        <th onclick="sortTable('dev-tbl',4)">Utilization</th>
        <th onclick="sortTable('dev-tbl',5)">Pwr Down</th>
        <th onclick="sortTable('dev-tbl',6)">Removed</th>
        <th onclick="sortTable('dev-tbl',7)">Current Milestone</th>
        <th onclick="sortTable('dev-tbl',8)">Milestone Status</th>
        <th onclick="sortTable('dev-tbl',9)">Milestone Start</th>
        <th onclick="sortTable('dev-tbl',10)">Power Savings (W)</th>
      </tr></thead>
      <tbody id="dev-tbl-body"></tbody>
    </table></div>
    <div id="dev-tbl-pager" style="display:none;align-items:center;gap:10px;margin-top:10px;font-size:.8rem">
      <button class="btn btn-outline" id="dev-prev" onclick="devPage(-1)">&#8592; Prev</button>
      <span id="dev-page-info"></span>
      <button class="btn btn-outline" id="dev-next" onclick="devPage(1)">Next &#8594;</button>
    </div>
  </div>
</div>

<!-- VELOCITY & RISK TAB -->
<div class="tab-pane" id="tab-velocity">
  <div class="kpi-row kpi-row-4">
    <div class="kpi c-green"><div class="kpi-label">Total Migrated</div><div class="kpi-value" id="vel-kpi-total">—</div><div class="kpi-sub">all time</div></div>
    <div class="kpi c-blue"><div class="kpi-label">Last Full Month</div><div class="kpi-value" id="vel-kpi-last">—</div><div class="kpi-sub" id="vel-kpi-last-sub">—</div></div>
    <div class="kpi c-purple"><div class="kpi-label">3-Month Avg</div><div class="kpi-value" id="vel-kpi-avg">—</div><div class="kpi-sub">circuits / month</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Projected Duration</div><div class="kpi-value" id="vel-kpi-proj">—</div><div class="kpi-sub">months at current pace</div></div>
  </div>
  <div class="card" style="margin-bottom:16px">
    <div class="card-title">Monthly Migration Completions</div>
    <div id="chart-velocity" style="height:340px"></div>
    <div style="font-size:.75rem;color:#adb5bd;margin-top:6px;padding:0 4px">
      &#9432; Completions are recorded when <code>MIGRATION_COMPLETE_DATE</code> is set. Recent months may be understated if dates lag behind actual work.
    </div>
  </div>
  <div class="row2" style="margin-bottom:16px">
    <div class="card"><div class="card-title">Risk Distribution</div><div id="chart-risk-donut" style="height:280px"></div></div>
    <div class="card"><div class="card-title">Remaining Circuits by Risk Level</div><div id="chart-risk-bar" style="height:280px"></div></div>
  </div>
  <div class="card">
    <div class="card-title">Cutover Risk by Switch</div>
    <div style="font-size:.75rem;color:#adb5bd;margin-bottom:10px">
      All planned cutover dates are in 2017–2019 — every switch with a date set is past due. Risk is classified by % migration complete.
    </div>
    <div class="filters">
      <label>Risk:</label>
      <select id="risk-filter-risk" onchange="renderRiskTable()"><option value="">All</option></select>
      <label>State:</label>
      <select id="risk-filter-state" onchange="renderRiskTable()"><option value="">All</option></select>
      <label>Status:</label>
      <select id="risk-filter-status" onchange="renderRiskTable()"><option value="">All</option></select>
      <label>CLLI search:</label>
      <input type="text" id="risk-search" placeholder="CLLI or wire center..." oninput="renderRiskTable()" style="width:180px">
    </div>
    <div class="tbl-wrap"><table id="risk-tbl">
      <thead><tr>
        <th onclick="sortTable('risk-tbl',0)">CLLI</th>
        <th onclick="sortTable('risk-tbl',1)">Wire Center</th>
        <th onclick="sortTable('risk-tbl',2)">State</th>
        <th onclick="sortTable('risk-tbl',3)">Switch Type</th>
        <th onclick="sortTable('risk-tbl',4)">Status</th>
        <th onclick="sortTable('risk-tbl',5)">Cutover Date</th>
        <th onclick="sortTable('risk-tbl',6)">Total Circuits</th>
        <th onclick="sortTable('risk-tbl',7)">Completed</th>
        <th onclick="sortTable('risk-tbl',8)">Remaining</th>
        <th onclick="sortTable('risk-tbl',9)">% Done</th>
        <th onclick="sortTable('risk-tbl',10)">Risk</th>
      </tr></thead>
      <tbody id="risk-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- PROGRAM KPIs TAB -->
<div class="tab-pane" id="tab-kpis">

  <!-- Program Health -->
  <div class="kpi-row" style="grid-template-columns:repeat(4,1fr);margin-bottom:6px">
    <div class="kpi c-blue"><div class="kpi-label">Total COs (CLLIs)</div><div class="kpi-value" id="kpi2-cllis">—</div><div class="kpi-sub">with active circuits</div></div>
    <div class="kpi c-purple"><div class="kpi-label">Switch Records</div><div class="kpi-value" id="kpi2-sw">—</div><div class="kpi-sub">in BT_NT_RETIREMENT</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Total Circuits</div><div class="kpi-value" id="kpi2-tot">—</div><div class="kpi-sub">non-SWIFT COs</div></div>
    <div class="kpi c-green"><div class="kpi-label">Circuits Completed</div><div class="kpi-value" id="kpi2-done">—</div><div class="kpi-sub" id="kpi2-done-sub">—</div></div>
  </div>
  <div class="kpi-row" style="grid-template-columns:repeat(4,1fr);margin-bottom:18px">
    <div class="kpi c-red"><div class="kpi-label">Circuits Remaining</div><div class="kpi-value" id="kpi2-rem">—</div><div class="kpi-sub">not yet migrated</div></div>
    <div class="kpi c-teal"><div class="kpi-label">3-Month Avg Pace</div><div class="kpi-value" id="kpi2-pace">—</div><div class="kpi-sub">circuits / month</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Projected Duration</div><div class="kpi-value" id="kpi2-proj">—</div><div class="kpi-sub">months at current pace</div></div>
    <div class="kpi c-blue"><div class="kpi-label">Last Active Month</div><div class="kpi-value" id="kpi2-lastmo" style="font-size:1.2rem">—</div><div class="kpi-sub" id="kpi2-lastmo-sub">—</div></div>
  </div>

  <!-- Site Activity Chart -->
  <div class="card" style="margin-bottom:16px">
    <div class="card-title">Monthly Site Activity — Circuits Completed &amp; Active CLLIs</div>
    <div id="chart-kpi-activity" style="height:340px"></div>
    <div style="font-size:.75rem;color:#adb5bd;margin-top:6px;padding:0 4px">
      &#9432; Circuits recorded when <code>MIGRATION_COMPLETE_DATE</code> is set in NT_DECOM_CIRCUITS_SOURCE. Recent months may be understated if dates lag actual work. No completions recorded after Jan 2026 as of this data pull.
    </div>
  </div>

  <!-- Savings KPIs -->
  <div style="font-size:.78rem;font-weight:700;text-transform:uppercase;color:#6c757d;letter-spacing:.5px;margin-bottom:8px">
    Power Savings &amp; Asset Recovery
    <span id="kpi2-sav-note" style="font-weight:400;text-transform:none;letter-spacing:0;margin-left:8px"></span>
  </div>
  <div class="kpi-row" style="grid-template-columns:repeat(4,1fr);margin-bottom:18px">
    <div class="kpi c-green"><div class="kpi-label">Annual Power Savings</div><div class="kpi-value" id="kpi2-annual" style="font-size:1.4rem">—</div><div class="kpi-sub">per year (switches w/ data)</div></div>
    <div class="kpi c-blue"><div class="kpi-label">Actual Savings to Date</div><div class="kpi-value" id="kpi2-actual" style="font-size:1.4rem">—</div><div class="kpi-sub">cumulative realized</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Copper Footage</div><div class="kpi-value" id="kpi2-copper" style="font-size:1.4rem">—</div><div class="kpi-sub">total across all switches</div></div>
    <div class="kpi c-purple"><div class="kpi-label">Analog OE Lines</div><div class="kpi-value" id="kpi2-oe" style="font-size:1.4rem">—</div><div class="kpi-sub">total across all switches</div></div>
  </div>

  <div class="row2" style="margin-bottom:16px">
    <div class="card"><div class="card-title">Annual Power Savings by Switch Status</div><div id="chart-kpi-sav-status" style="height:280px"></div></div>
    <div class="card"><div class="card-title">Copper Footage by State</div><div id="chart-kpi-copper" style="height:280px"></div></div>
  </div>
  <div class="card">
    <div class="card-title">Analog OE by State</div>
    <div id="chart-kpi-oe" style="height:260px"></div>
  </div>

</div>

<!-- FLAGGED SITES TAB -->
<div class="tab-pane" id="tab-flagged">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
    <div class="kpi-row kpi-row-4" style="flex:1;margin-bottom:0">
      <div class="kpi c-orange"><div class="kpi-label">Flagged</div><div class="kpi-value" id="fl-kpi-flagged">—</div><div class="kpi-sub">require follow-up</div></div>
      <div class="kpi c-blue"><div class="kpi-label">Has Notes</div><div class="kpi-value" id="fl-kpi-notes">—</div><div class="kpi-sub">any note text saved</div></div>
      <div class="kpi c-purple"><div class="kpi-label">Total Entries</div><div class="kpi-value" id="fl-kpi-total">—</div><div class="kpi-sub">CLLIs with notes or flags</div></div>
      <div class="kpi c-green"><div class="kpi-label">Last Updated</div><div class="kpi-value" id="fl-kpi-latest" style="font-size:1rem">—</div><div class="kpi-sub">most recent save</div></div>
    </div>
    <div style="display:flex;gap:8px;align-self:flex-start;margin-top:4px">
      <button class="btn btn-primary" onclick="loadFlaggedSites()">&#8635; Refresh</button>
    </div>
  </div>

  <div id="fl-offline" style="display:none;padding:12px 16px;background:#fff3cd;border-radius:8px;font-size:.85rem;color:#664d03;margin-bottom:14px">
    &#128308; Cannot reach the notes server. Start <code>switch_decom_server.py</code> and click Refresh.
  </div>
  <div id="fl-loading" style="display:none;color:#6c757d;font-size:.85rem;padding:8px 0;margin-bottom:10px">
    <span style="display:inline-block;width:14px;height:14px;border:2px solid #dee2e6;border-top-color:#0d6efd;border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:6px"></span>
    Loading notes from server...
  </div>

  <div id="fl-content" style="display:none">
    <div class="card">
      <div class="card-title">Notes &amp; Flagged Sites</div>
      <div class="filters">
        <label>Show:</label>
        <select id="fl-filter-show" onchange="renderFlaggedTable()">
          <option value="">All</option>
          <option value="flagged">Flagged Only</option>
          <option value="notes">Has Notes</option>
        </select>
        <label>Region:</label>
        <select id="fl-filter-region" onchange="renderFlaggedTable()"><option value="">All</option></select>
        <label>State:</label>
        <select id="fl-filter-state" onchange="renderFlaggedTable()"><option value="">All</option></select>
        <input type="text" id="fl-search" placeholder="Search CLLI or note text..." oninput="renderFlaggedTable()" style="width:220px">
        <span id="fl-tbl-count" style="font-size:.8rem;color:#6c757d;margin-left:auto"></span>
      </div>
      <div class="tbl-wrap"><table id="fl-tbl">
        <thead><tr>
          <th onclick="sortTable('fl-tbl',0)">CLLI</th>
          <th onclick="sortTable('fl-tbl',1)">Wire Center</th>
          <th onclick="sortTable('fl-tbl',2)">Region</th>
          <th onclick="sortTable('fl-tbl',3)">State</th>
          <th onclick="sortTable('fl-tbl',4)">Switch Type</th>
          <th onclick="sortTable('fl-tbl',5)">Circuits</th>
          <th onclick="sortTable('fl-tbl',6)">% Done</th>
          <th onclick="sortTable('fl-tbl',7)">Flagged</th>
          <th onclick="sortTable('fl-tbl',8)">Note</th>
          <th onclick="sortTable('fl-tbl',9)">Last Updated</th>
        </tr></thead>
        <tbody id="fl-tbl-body"></tbody>
      </table></div>
    </div>
  </div>
</div>

<!-- DEPENDENCIES TAB -->
<div class="tab-pane" id="tab-depmap">
  <div class="kpi-row kpi-row-4">
    <div class="kpi c-red"><div class="kpi-label">Switches with Dependents</div><div class="kpi-value" id="dep-kpi-count">—</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Total Dependent WCs at Risk</div><div class="kpi-value" id="dep-kpi-wcs">—</div></div>
    <div class="kpi c-blue"><div class="kpi-label">Max Dependents (1 Switch)</div><div class="kpi-value" id="dep-kpi-max">—</div></div>
    <div class="kpi c-purple"><div class="kpi-label">Switches with No Dependents</div><div class="kpi-value" id="dep-kpi-none">—</div></div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:18px">
    <div class="card">
      <div class="card-title">Top 20 Switches by Dependent Wire Center Count</div>
      <div id="dep-bar-chart" style="height:420px"></div>
    </div>
    <div class="card">
      <div class="card-title">Dependency Depth Distribution</div>
      <div id="dep-donut-chart" style="height:420px"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Switch Dependency Table</div>
    <div class="filters">
      <label>Region:</label>
      <select id="dep-filter-region" onchange="renderDepTable()"><option value="">All Regions</option></select>
      <label>State:</label>
      <select id="dep-filter-state" onchange="renderDepTable()"><option value="">All States</option></select>
      <label>Has Dependents:</label>
      <select id="dep-filter-deps" onchange="renderDepTable()">
        <option value="">All</option>
        <option value="yes">Has Dependents</option>
        <option value="no">No Dependents</option>
      </select>
      <input type="text" id="dep-search" placeholder="Search CLLI..." oninput="renderDepTable()" style="width:180px">
      <span id="dep-tbl-count" style="font-size:.8rem;color:#6c757d;margin-left:auto"></span>
    </div>
    <div class="tbl-wrap"><table id="dep-tbl">
      <thead><tr>
        <th onclick="sortTable('dep-tbl',0)">CLLI</th>
        <th onclick="sortTable('dep-tbl',1)">State</th>
        <th onclick="sortTable('dep-tbl',2)">Region</th>
        <th onclick="sortTable('dep-tbl',3)">Switch Type</th>
        <th onclick="sortTable('dep-tbl',4)">% Done</th>
        <th onclick="sortTable('dep-tbl',5)">Risk</th>
        <th onclick="sortTable('dep-tbl',6)">Upstream Switch(es)</th>
        <th onclick="sortTable('dep-tbl',7)"># Dependents</th>
        <th onclick="sortTable('dep-tbl',8)">Dependent Wire Centers</th>
      </tr></thead>
      <tbody id="dep-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- SITE LOOKUP TAB -->
<div class="tab-pane" id="tab-lookup">
  <div class="lookup-box">
    <label style="display:block;font-weight:700;margin-bottom:6px;font-size:.85rem">Enter CLLI Code:</label>
    <input type="text" id="lookup-input" placeholder="e.g. NYCMNYWS" oninput="doLookup()" style="text-transform:uppercase">
  </div>
  <div id="lookup-none">No data found for that CLLI. Try an 8-character CLLI code (e.g. NYCMNYWS).</div>
  <div class="lookup-result" id="lookup-result" style="display:none">
    <div class="info-card" id="lookup-switch">
      <h3>&#128225; Switch Inventory</h3>
      <div id="lookup-switch-body"></div>
    </div>
    <div class="info-card" id="lookup-circuits">
      <h3>&#128203; Circuit Summary</h3>
      <div id="lookup-circuit-body"></div>
    </div>
  </div>
  <div id="lookup-notes-wrap" style="display:none;max-width:820px">
    <div class="notes-panel">
      <h4>&#128221; Notes &amp; Flag — <span id="notes-clli-label"></span></h4>
      <textarea id="notes-text" placeholder="Add notes for this CO..."></textarea>
      <div class="notes-actions">
        <button class="btn btn-primary" onclick="saveNote()">Save Note</button>
        <label class="flag-toggle" id="flag-label">
          <input type="checkbox" id="flag-check" onchange="saveNote()"> Flag for follow-up
        </label>
        <span class="notes-saved" id="notes-saved-msg">&#10003; Saved</span>
        <span id="notes-updated" style="font-size:.75rem;color:#adb5bd;margin-left:auto"></span>
      </div>
    </div>
  </div>
  <div id="notes-server-warn" style="display:none;margin-top:10px;font-size:.8rem;color:#adb5bd;font-style:italic">
    &#128308; Notes unavailable — start switch_decom_server.py to enable write-back.
  </div>

  <div id="lookup-devices-wrap" style="display:none;max-width:1100px;margin-top:18px">
    <div class="card">
      <div class="card-title">&#128225; Device Inventory — <span id="lookup-dev-clli-label"></span></div>
      <div class="kpi-row kpi-row-4" style="margin-bottom:14px">
        <div class="kpi c-purple"><div class="kpi-label">Devices</div><div class="kpi-value" id="lu-dev-count">—</div></div>
        <div class="kpi c-blue"><div class="kpi-label">Circuits on Devices</div><div class="kpi-value" id="lu-dev-ckts">—</div></div>
        <div class="kpi c-green"><div class="kpi-label">Powered Down</div><div class="kpi-value" id="lu-dev-pwrdn">—</div></div>
        <div class="kpi c-orange"><div class="kpi-label">Power Savings (W)</div><div class="kpi-value" id="lu-dev-pwr">—</div></div>
      </div>
      <div class="tbl-wrap" style="max-height:380px">
        <table id="lu-dev-tbl" style="font-size:.8rem">
          <thead><tr>
            <th onclick="sortTable('lu-dev-tbl',0)">Device Name</th>
            <th onclick="sortTable('lu-dev-tbl',1)">Device Type</th>
            <th onclick="sortTable('lu-dev-tbl',2)">Circuits</th>
            <th onclick="sortTable('lu-dev-tbl',3)">Utilization</th>
            <th onclick="sortTable('lu-dev-tbl',4)">Pwr Down</th>
            <th onclick="sortTable('lu-dev-tbl',5)">Removed</th>
            <th onclick="sortTable('lu-dev-tbl',6)">Current Milestone</th>
            <th onclick="sortTable('lu-dev-tbl',7)">Milestone Status</th>
            <th onclick="sortTable('lu-dev-tbl',8)">Milestone Start</th>
            <th onclick="sortTable('lu-dev-tbl',9)">Power Savings (W)</th>
          </tr></thead>
          <tbody id="lu-dev-tbl-body"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div id="lookup-dep-wrap" style="display:none;max-width:1100px;margin-top:18px">
    <div class="card">
      <div class="card-title">&#128257; Switch Dependencies — <span id="lookup-dep-clli-label"></span></div>
      <div id="lookup-dep-body"></div>
    </div>
  </div>

  <div id="ckt-detail-wrap" style="display:none;max-width:1100px;margin-top:18px">
    <div class="card">
      <div class="card-title" id="ckt-detail-title">Circuit Detail</div>
      <div id="ckt-detail-loading" style="color:#6c757d;font-size:.85rem;padding:8px 0">
        <span style="display:inline-block;width:14px;height:14px;border:2px solid #dee2e6;border-top-color:#0d6efd;border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:6px"></span>
        Loading circuits from Oracle...
      </div>
      <div id="ckt-detail-content" style="display:none">
        <div class="filters" style="margin-bottom:10px">
          <label>Status:</label>
          <select id="ckt-det-filter-status" onchange="renderCktDetail()">
            <option value="">All</option>
            <option value="Y">Completed</option>
            <option value="P">In Progress</option>
            <option value="N">Pending</option>
          </select>
          <label>Type:</label>
          <select id="ckt-det-filter-type" onchange="renderCktDetail()"><option value="">All Types</option></select>
          <input type="text" id="ckt-det-search" placeholder="Search ID / Customer..." oninput="renderCktDetail()" style="width:200px">
          <span id="ckt-det-count" style="font-size:.8rem;color:#6c757d;margin-left:auto"></span>
        </div>
        <div class="tbl-wrap" style="max-height:420px">
          <table id="ckt-det-tbl" style="font-size:.78rem">
            <thead><tr>
              <th>Identifier (WTN)</th>
              <th>USOC</th>
              <th>Circuit Type</th>
              <th>Status</th>
              <th>Mig. Date</th>
              <th>Wave</th>
              <th>C/F</th>
              <th>Customer</th>
            </tr></thead>
            <tbody id="ckt-det-body"></tbody>
          </table>
        </div>
        <div id="ckt-det-pager" style="display:none;align-items:center;gap:10px;margin-top:10px;font-size:.8rem">
          <button class="btn btn-outline" id="ckt-det-prev" onclick="cktDetPage(-1)">&#8592; Prev</button>
          <span id="ckt-det-page-info"></span>
          <button class="btn btn-outline" id="ckt-det-next" onclick="cktDetPage(1)">Next &#8594;</button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
""" + plotly_js + """
</script>
<script>
const DATA = """ + data_json + """;

const fmt  = n => n == null ? '—' : Number(n).toLocaleString();
const pct  = (a,b) => b ? (a/b*100).toFixed(1)+'%' : '0.0%';
const fmtM = v => v == null || v==='' ? '—' : '$'+Number(v).toLocaleString(undefined,{maximumFractionDigits:0});

// ── TIRKS CLLI set (built once, used across tabs) ────────────────────────────
const TIRKS_CLLIS = new Set(DATA.devices.BUILDING_CLLI.filter(Boolean));
const tirksBadge = clli =>
  TIRKS_CLLIS.has(clli)
    ? '<span class="badge" style="background:#d1ecf1;color:#0c5460">TIRKS</span>'
    : '—';

// ── Tabs ─────────────────────────────────────────────────────────────────────
function showTab(id, btn){
  document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  btn.classList.add('active');
}

// ── Sort tables ───────────────────────────────────────────────────────────────
let sortState = {};
function sortTable(id, col){
  const tbl = document.getElementById(id);
  const tbody = tbl.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const key = id+'_'+col;
  const asc = sortState[key] = !sortState[key];
  rows.sort((a,b)=>{
    const va = a.cells[col]?.dataset?.val ?? a.cells[col]?.textContent ?? '';
    const vb = b.cells[col]?.dataset?.val ?? b.cells[col]?.textContent ?? '';
    const na = parseFloat(va), nb = parseFloat(vb);
    if(!isNaN(na)&&!isNaN(nb)) return asc ? na-nb : nb-na;
    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
  });
  rows.forEach(r=>tbody.appendChild(r));
}

// ── Init Summary ─────────────────────────────────────────────────────────────
function initSummary(){
  const S = DATA.summary;
  document.getElementById('hdr-sub').textContent =
    'Non-SWIFT COs only  |  ' + S.swift_excluded + ' SWIFT CLLIs excluded  |  Data as of ' + S.as_of;
  document.getElementById('kpi-cllis').textContent = fmt(S.total_cllis);
  document.getElementById('kpi-sw').textContent    = fmt(S.total_switches);
  document.getElementById('kpi-tot').textContent   = fmt(S.total_circuits);
  document.getElementById('kpi-done').textContent  = fmt(S.completed);
  document.getElementById('kpi-done-sub').textContent = pct(S.completed, S.total_circuits) + ' complete';
  document.getElementById('kpi-rem').textContent   = fmt(S.pending + S.in_progress);

  // Status donut
  Plotly.newPlot('chart-status-donut', [{
    type:'pie', hole:.45,
    labels:['Completed','In Progress','Pending'],
    values:[S.completed, S.in_progress, S.pending],
    marker:{colors:['#198754','#fd7e14','#dc3545']},
    textinfo:'label+percent', hovertemplate:'%{label}: %{value:,}<extra></extra>'
  }], {margin:{t:10,b:10,l:10,r:10}, showlegend:true, legend:{orientation:'h',y:-0.1}}, {responsive:true, displayModeBar:false});

  // Region bar
  const R = DATA.regions;
  Plotly.newPlot('chart-region-bar', [
    {name:'Completed',   type:'bar', x:R.REGION, y:R.completed,   marker:{color:'#198754'}},
    {name:'In Progress', type:'bar', x:R.REGION, y:R.in_progress, marker:{color:'#fd7e14'}},
    {name:'Pending',     type:'bar', x:R.REGION,
     y:R.total.map((t,i)=>t-R.completed[i]-R.in_progress[i]), marker:{color:'#dee2e6'}}
  ], {barmode:'stack', margin:{t:10,b:80,l:60,r:10},
      legend:{orientation:'h',y:-0.25},
      xaxis:{tickangle:-30}, yaxis:{tickformat:','}}, {responsive:true, displayModeBar:false});

  // C vs F donut
  const totCopper = R.copper.reduce((a,b)=>a+b,0);
  const totFiber  = R.fiber.reduce((a,b)=>a+b,0);
  const totUnk    = S.total_circuits - totCopper - totFiber;
  Plotly.newPlot('chart-cf-donut', [{
    type:'pie', hole:.45,
    labels:['Copper','Fiber','Unknown'],
    values:[totCopper, totFiber, totUnk],
    marker:{colors:['#fd7e14','#0d6efd','#adb5bd']},
    textinfo:'label+percent', hovertemplate:'%{label}: %{value:,}<extra></extra>'
  }], {margin:{t:10,b:10,l:10,r:10}, showlegend:true, legend:{orientation:'h',y:-0.1}}, {responsive:true, displayModeBar:false});

  // Segment bar
  const SG = DATA.segments;
  Plotly.newPlot('chart-seg-bar', [{
    type:'bar', orientation:'h',
    x:SG.TOTAL.slice(0,8), y:SG.SEGMENT.slice(0,8),
    marker:{color:'#0d6efd'},
    hovertemplate:'%{y}: %{x:,}<extra></extra>'
  }], {margin:{t:10,b:40,l:180,r:60},
       xaxis:{tickformat:','}, yaxis:{automargin:true}}, {responsive:true, displayModeBar:false});
}

// ── Switch tab CLLI lookup ────────────────────────────────────────────────────
function doSwLookup(){
  const q = document.getElementById('sw-lookup-input').value.trim().toUpperCase();
  const resEl  = document.getElementById('sw-lookup-result');
  const noneEl = document.getElementById('sw-lookup-none');
  if(q.length < 3){ resEl.style.display='none'; noneEl.style.display='none'; return; }
  const SW = DATA.switches;
  const si = SW.CLLI.indexOf(q);
  if(si === -1){ resEl.style.display='none'; noneEl.style.display='block'; return; }
  noneEl.style.display='none'; resEl.style.display='block';
  document.getElementById('sw-lookup-title').textContent = SW.CLLI[si] + '  —  ' + SW.WIRE_CENTER[si];
  loadNote(q);
  document.getElementById('sw-lookup-body').innerHTML =
      row('State / Region', SW.STATE[si] + ' / ' + SW.REGION[si])
    + row('Switch Type', SW.SWITCH_TYPE[si]||'—')
    + row('Status', SW.SWITCH_STATUS[si]||'—')
    + row('Cutover Year', SW.CUTOVER_YEAR[si]||'—')
    + row('Cutover Date', SW.SWITCH_CUTOVER_DATE[si]||'—')
    + row('Annual Power Savings', fmtM(SW.ANNUAL_POWER_SAVINGS[si]))
    + row('Total Savings', fmtM(SW.TOTAL_ACTUAL_SAVINGS_PRTXDIS[si]))
    + row('Copper Footage', SW.COPPER_FOOTAGE[si] ? Number(SW.COPPER_FOOTAGE[si]).toLocaleString()+' ft' : '—')
    + row('Analog OE', SW.ANALOG_OE[si]||'—')
    + row('PG Universal OE', SW.PG__UNIVERSAL[si]||'—')
    + row('PG Integrated OE', SW.PG__INTEGRATED[si]||'—')
    + row('OLT/FTTP OE', SW.OLTFTTP[si]||'—');
}

// ── Init Switches ──────────────────────────────────────────────────────────────
function initSwitches(){
  const SW = DATA.switches;
  const n = SW.CLLI.length;
  const remove = SW.SWITCH_STATUS.filter(s=>s==='Remove').length;
  const aip    = SW.SWITCH_STATUS.filter(s=>s==='AIP').length;
  document.getElementById('sw-kpi-tot').textContent   = fmt(n);
  document.getElementById('sw-kpi-rem').textContent   = fmt(remove);
  document.getElementById('sw-kpi-aip').textContent   = fmt(aip);
  document.getElementById('sw-kpi-swift').textContent = fmt(DATA.summary.swift_excluded);

  // Switch type donut
  const typeCounts = {};
  SW.SWITCH_TYPE.forEach(t=>{ if(t) typeCounts[t]=(typeCounts[t]||0)+1; });
  const types = Object.keys(typeCounts).sort((a,b)=>typeCounts[b]-typeCounts[a]);
  Plotly.newPlot('chart-sw-type', [{
    type:'pie', hole:.4,
    labels:types, values:types.map(t=>typeCounts[t]),
    textinfo:'label+value', hovertemplate:'%{label}: %{value}<extra></extra>'
  }], {margin:{t:10,b:10,l:10,r:10}, showlegend:false}, {responsive:true, displayModeBar:false});

  // By state bar
  const stateCounts = {};
  SW.STATE.forEach(s=>{ if(s) stateCounts[s]=(stateCounts[s]||0)+1; });
  const states = Object.keys(stateCounts).sort((a,b)=>stateCounts[b]-stateCounts[a]);
  Plotly.newPlot('chart-sw-state', [{
    type:'bar', x:states, y:states.map(s=>stateCounts[s]),
    marker:{color:'#6f42c1'},
    hovertemplate:'%{x}: %{y}<extra></extra>'
  }], {margin:{t:10,b:50,l:50,r:10}, yaxis:{dtick:10}}, {responsive:true, displayModeBar:false});

  // Populate filter dropdowns
  const swStateEl  = document.getElementById('sw-filter-state');
  const swRegionEl = document.getElementById('sw-filter-region');
  const swTypeEl   = document.getElementById('sw-filter-type');
  const swStatEl   = document.getElementById('sw-filter-status');
  [...new Set(SW.STATE)].filter(Boolean).sort().forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; swStateEl.appendChild(o); });
  [...new Set(SW.REGION)].filter(Boolean).sort().forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; swRegionEl.appendChild(o); });
  [...new Set(SW.SWITCH_TYPE)].filter(Boolean).sort().forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; swTypeEl.appendChild(o); });
  [...new Set(SW.SWITCH_STATUS)].filter(Boolean).sort().forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; swStatEl.appendChild(o); });

  renderSwTable();
}

function renderSwTable(){
  const SW = DATA.switches;
  const fState  = document.getElementById('sw-filter-state').value;
  const fRegion = document.getElementById('sw-filter-region').value;
  const fType   = document.getElementById('sw-filter-type').value;
  const fStatus = document.getElementById('sw-filter-status').value;
  const fTirks  = document.getElementById('sw-filter-tirks').value;
  const tbody = document.getElementById('sw-tbl-body');
  let html = '';
  for(let i=0; i<SW.CLLI.length; i++){
    if(fState  && SW.STATE[i]!==fState)  continue;
    if(fRegion && SW.REGION[i]!==fRegion) continue;
    if(fType   && SW.SWITCH_TYPE[i]!==fType) continue;
    if(fStatus && SW.SWITCH_STATUS[i]!==fStatus) continue;
    if(fTirks === 'Y' && !TIRKS_CLLIS.has(SW.CLLI[i])) continue;
    if(fTirks === 'N' &&  TIRKS_CLLIS.has(SW.CLLI[i])) continue;
    const status = SW.SWITCH_STATUS[i]||'';
    const badge = status==='Remove'
      ? '<span class="badge badge-remove">Remove</span>'
      : status==='AIP'
      ? '<span class="badge badge-aip">AIP</span>'
      : status||'—';
    const sav = SW.ANNUAL_POWER_SAVINGS[i];
    const cf  = SW.COPPER_FOOTAGE[i];
    html += '<tr>'
      +'<td>'+SW.CLLI[i]+'</td>'
      +'<td>'+SW.WIRE_CENTER[i]+'</td>'
      +'<td>'+SW.STATE[i]+'</td>'
      +'<td>'+SW.REGION[i]+'</td>'
      +'<td>'+SW.SWITCH_TYPE[i]+'</td>'
      +'<td>'+badge+'</td>'
      +'<td>'+(SW.CUTOVER_YEAR[i]||'—')+'</td>'
      +'<td data-val="'+(sav||0)+'">'+fmtM(sav)+'</td>'
      +'<td data-val="'+(cf||0)+'">'+(cf?Number(cf).toLocaleString():'—')+'</td>'
      +'<td>'+(SW.ANALOG_OE[i]||'—')+'</td>'
      +'<td>'+tirksBadge(SW.CLLI[i])+'</td>'
      +'</tr>';
  }
  tbody.innerHTML = html || '<tr><td colspan="11" style="text-align:center;color:#adb5bd;padding:20px">No records match filters</td></tr>';
}

// ── Init Circuits ──────────────────────────────────────────────────────────────
function initCircuits(){
  const CK = DATA.circuits;
  const regionEl  = document.getElementById('ckt-filter-region');
  const stateEl   = document.getElementById('ckt-filter-state');
  const swtypeEl  = document.getElementById('ckt-filter-swtype');
  [...new Set(CK.REGION)].filter(Boolean).sort().forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; regionEl.appendChild(o); });
  [...new Set(CK.STATE)].filter(Boolean).sort().forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; stateEl.appendChild(o); });
  [...new Set(CK.SWITCH_TYPE)].filter(Boolean).sort().forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; swtypeEl.appendChild(o); });
  renderCktTable();
  renderCktTypeCharts();
}

// ── Circuit Type Charts ────────────────────────────────────────────────────────
function renderCktTypeCharts(){
  const CT = DATA.cktypes;
  if(!CT || !CT.CKT_TYPE || CT.CKT_TYPE.length === 0){
    document.getElementById('cktype-charts').style.display = 'none';
    return;
  }
  const topN = Math.min(CT.CKT_TYPE.length, 12);
  const labels = CT.CKT_TYPE.slice(0, topN);
  const totals = CT.TOTAL.slice(0, topN);
  const done   = CT.COMPLETED.slice(0, topN);
  const ppArr  = totals.map((t,i) => t ? +((done[i]||0)/t*100).toFixed(1) : 0);

  document.getElementById('cktype-dist-title').textContent = 'Circuit Type Distribution (by USOC)';

  // Donut — volume by type
  Plotly.newPlot('chart-cktype-donut', [{
    type:'pie', hole:.42,
    labels: labels,
    values: totals,
    textinfo:'label+percent',
    hovertemplate:'%{label}<br>%{value:,} circuits (%{percent})<extra></extra>'
  }], {margin:{t:10,b:10,l:10,r:10}, showlegend:false}, {responsive:true, displayModeBar:false});

  // Horizontal bar — completion rate per type with total count annotation
  const barColors = ppArr.map(v => v>=75 ? '#198754' : v>=25 ? '#fd7e14' : '#dc3545');
  Plotly.newPlot('chart-cktype-bar', [{
    type:'bar', orientation:'h',
    y: labels.slice().reverse(),
    x: ppArr.slice().reverse(),
    marker:{color: barColors.slice().reverse()},
    text: totals.slice().reverse().map((t,i) => fmt(t) + ' ckts'),
    textposition:'outside',
    hovertemplate:'%{y}<br>%{x:.1f}% complete<extra></extra>'
  }], {
    margin:{t:10, b:40, l:160, r:80},
    xaxis:{title:'% Complete', range:[0,115], ticksuffix:'%'},
    yaxis:{automargin:true},
    shapes:[{type:'line',x0:100,x1:100,y0:-0.5,y1:labels.length-0.5,
             line:{color:'#198754',dash:'dot',width:1}}]
  }, {responsive:true, displayModeBar:false});
}

function renderCktTable(){
  const CK = DATA.circuits;
  const fRegion = document.getElementById('ckt-filter-region').value;
  const fState  = document.getElementById('ckt-filter-state').value;
  const fSwtype = document.getElementById('ckt-filter-swtype').value;
  const fSearch = document.getElementById('ckt-search').value.trim().toUpperCase();
  const fTirks  = document.getElementById('ckt-filter-tirks').value;
  let filtCllis=0, filtTot=0, filtDone=0, filtRem=0;
  const tbody = document.getElementById('ckt-tbl-body');
  let html = '';
  for(let i=0; i<CK.CLLI_CD.length; i++){
    if(fRegion && CK.REGION[i]!==fRegion) continue;
    if(fState  && CK.STATE[i]!==fState)   continue;
    if(fSwtype && CK.SWITCH_TYPE[i]!==fSwtype) continue;
    if(fSearch && !(CK.CLLI_CD[i]||'').includes(fSearch) && !(CK.WC_NAME[i]||'').toUpperCase().includes(fSearch)) continue;
    if(fTirks === 'Y' && !TIRKS_CLLIS.has(CK.CLLI_CD[i])) continue;
    if(fTirks === 'N' &&  TIRKS_CLLIS.has(CK.CLLI_CD[i])) continue;
    const tot  = CK.TOTAL[i]||0;
    const done = CK.COMPLETED[i]||0;
    const prog = CK.IN_PROGRESS[i]||0;
    const rem  = tot-done-prog;
    const pp   = tot ? (done/tot*100) : 0;
    const waveStr = (CK.MIN_WAVE[i]&&CK.MAX_WAVE[i])
      ? (CK.MIN_WAVE[i]===CK.MAX_WAVE[i] ? CK.MIN_WAVE[i] : CK.MIN_WAVE[i]+'-'+CK.MAX_WAVE[i])
      : '—';
    filtCllis++; filtTot+=tot; filtDone+=done; filtRem+=rem;
    html += '<tr onclick="lookupCLLI(this.dataset.clli)" data-clli="'+CK.CLLI_CD[i]+'" style="cursor:pointer">'
      +'<td style="color:#0d6efd;font-weight:600;text-decoration:underline">'+CK.CLLI_CD[i]+'</td>'
      +'<td>'+CK.WC_NAME[i]+'</td>'
      +'<td>'+CK.REGION[i]+'</td>'
      +'<td>'+CK.STATE[i]+'</td>'
      +'<td data-val="'+tot+'">'+fmt(tot)+'</td>'
      +'<td data-val="'+done+'">'+fmt(done)+'</td>'
      +'<td data-val="'+prog+'">'+fmt(prog)+'</td>'
      +'<td data-val="'+rem+'">'+fmt(rem)+'</td>'
      +'<td data-val="'+pp.toFixed(1)+'">'
        +'<div style="display:flex;align-items:center;gap:6px">'
        +'<div class="pbar"><div class="pbar-fill" style="width:'+Math.min(pp,100)+'%"></div></div>'
        +'<span style="font-size:.75rem;min-width:36px">'+pp.toFixed(1)+'%</span>'
        +'</div></td>'
      +'<td>'+(CK.SWITCH_TYPE[i]||'—')+'</td>'
      +'<td>'+waveStr+'</td>'
      +'<td>'+tirksBadge(CK.CLLI_CD[i])+'</td>'
      +'</tr>';
  }
  tbody.innerHTML = html || '<tr><td colspan="12" style="text-align:center;color:#adb5bd;padding:20px">No records match filters</td></tr>';
  document.getElementById('ckt-kpi-cllis').textContent = fmt(filtCllis);
  document.getElementById('ckt-kpi-tot').textContent   = fmt(filtTot);
  document.getElementById('ckt-kpi-done').textContent  = fmt(filtDone);
  document.getElementById('ckt-kpi-rem').textContent   = fmt(filtRem);
}

// ── Init Waves ────────────────────────────────────────────────────────────────
function initWaves(){
  const WV = DATA.waves;
  const lbl   = WV.WAVE.map(String);
  const rem   = WV.TOTAL.map((t,i)=>t-(WV.COMPLETED[i]||0));
  const ppArr = WV.TOTAL.map((t,i)=>t ? ((WV.COMPLETED[i]||0)/t*100) : 0);

  Plotly.newPlot('chart-wave-bar', [
    {name:'Completed', type:'bar', x:lbl, y:WV.COMPLETED, marker:{color:'#198754'}},
    {name:'Remaining', type:'bar', x:lbl, y:rem,           marker:{color:'#dee2e6'}}
  ], {barmode:'stack', margin:{t:10,b:80,l:60,r:10},
      xaxis:{tickangle:-45,title:'Wave'}, yaxis:{tickformat:','},
      legend:{orientation:'h',y:-0.3}}, {responsive:true, displayModeBar:false});

  Plotly.newPlot('chart-wave-pct', [{
    type:'bar', x:lbl, y:ppArr.map(v=>+v.toFixed(1)),
    marker:{color:ppArr.map(v=>v>=75?'#198754':v>=25?'#fd7e14':'#dc3545')},
    hovertemplate:'Wave %{x}: %{y:.1f}%<extra></extra>'
  }], {margin:{t:10,b:80,l:60,r:10},
       xaxis:{tickangle:-45,title:'Wave'}, yaxis:{title:'% Complete',range:[0,100]},
       shapes:[{type:'line',x0:-0.5,x1:lbl.length-0.5,y0:100,y1:100,line:{color:'#198754',dash:'dot',width:1}}]},
      {responsive:true, displayModeBar:false});

  const tbody = document.getElementById('wave-tbl-body');
  let html = '';
  WV.WAVE.forEach((w,i)=>{
    const tot  = WV.TOTAL[i]||0;
    const done = WV.COMPLETED[i]||0;
    const r    = tot-done;
    const p    = tot ? (done/tot*100).toFixed(1) : '0.0';
    html += '<tr><td>'+w+'</td><td>'+fmt(tot)+'</td><td>'+fmt(done)+'</td><td>'+fmt(r)+'</td>'
          +'<td><div style="display:flex;align-items:center;gap:6px">'
          +'<div class="pbar"><div class="pbar-fill" style="width:'+Math.min(parseFloat(p),100)+'%"></div></div>'
          +'<span style="font-size:.75rem">'+p+'%</span></div></td></tr>';
  });
  tbody.innerHTML = html;
}

// ── Site Lookup ────────────────────────────────────────────────────────────────
function lookupCLLI(clli){
  document.getElementById('lookup-input').value = clli;
  showTab('lookup', document.querySelectorAll('.tab-btn')[9]);
  doLookup();
  window.scrollTo(0,0);
}

function doLookup(){
  const q = document.getElementById('lookup-input').value.trim().toUpperCase();
  const resEl  = document.getElementById('lookup-result');
  const noneEl = document.getElementById('lookup-none');
  if(q.length < 3){ resEl.style.display='none'; noneEl.style.display='none';
    document.getElementById('lookup-devices-wrap').style.display='none'; return; }

  const CK = DATA.circuits;
  const SW = DATA.switches;
  const ci = CK.CLLI_CD.indexOf(q);
  const si = SW.CLLI.indexOf(q);

  if(ci===-1 && si===-1){
    resEl.style.display='none'; noneEl.style.display='block'; return;
  }
  noneEl.style.display='none'; resEl.style.display='grid';
  loadNote(q);
  loadCircuits(q);
  loadLookupDeps(q);
  loadLookupDevices(q);

  // Switch card
  let swHtml = '';
  if(si !== -1){
    swHtml += row('CLLI', SW.CLLI[si])
      + row('Wire Center', SW.WIRE_CENTER[si])
      + row('State', SW.STATE[si])
      + row('Region', SW.REGION[si])
      + row('Switch Type', SW.SWITCH_TYPE[si])
      + row('Status', SW.SWITCH_STATUS[si])
      + row('Cutover Year', SW.CUTOVER_YEAR[si]||'—')
      + row('Cutover Date', SW.SWITCH_CUTOVER_DATE[si]||'—')
      + row('Annual Power Savings', fmtM(SW.ANNUAL_POWER_SAVINGS[si]))
      + row('Copper Footage', SW.COPPER_FOOTAGE[si] ? Number(SW.COPPER_FOOTAGE[si]).toLocaleString()+' ft' : '—')
      + row('Analog OE', SW.ANALOG_OE[si]||'—');
  } else {
    swHtml = '<div style="color:#adb5bd;font-size:.85rem;padding:10px">Not found in switch inventory (BT_NT_RETIREMENT).</div>';
  }
  document.getElementById('lookup-switch-body').innerHTML = swHtml;

  // Circuit card
  let ckHtml = '';
  if(ci !== -1){
    const tot  = CK.TOTAL[ci]||0;
    const done = CK.COMPLETED[ci]||0;
    const prog = CK.IN_PROGRESS[ci]||0;
    const rem  = tot-done-prog;
    const pp   = tot ? (done/tot*100).toFixed(1) : '0.0';
    const waveStr = (CK.MIN_WAVE[ci]&&CK.MAX_WAVE[ci])
      ? (CK.MIN_WAVE[ci]===CK.MAX_WAVE[ci] ? 'Wave '+CK.MIN_WAVE[ci] : 'Waves '+CK.MIN_WAVE[ci]+' – '+CK.MAX_WAVE[ci])
      : '—';
    ckHtml += row('Wire Center', CK.WC_NAME[ci]||'—')
      + row('Region', CK.REGION[ci]||'—')
      + row('State', CK.STATE[ci]||'—')
      + row('Total Circuits', fmt(tot))
      + row('Completed', fmt(done)+' ('+pp+'%)')
      + row('In Progress', fmt(prog))
      + row('Remaining', fmt(rem))
      + row('Copper Circuits', fmt(CK.COPPER[ci]||0))
      + row('Fiber Circuits', fmt(CK.FIBER[ci]||0))
      + row('Wave Range', waveStr);
    const pp2 = parseFloat(pp);
    ckHtml += '<div style="margin-top:14px"><div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#6c757d;margin-bottom:6px">Migration Progress</div>'
      +'<div class="pbar" style="height:14px"><div class="pbar-fill" style="width:'+Math.min(pp2,100)+'%;height:14px;background:'+(pp2>=75?'#198754':pp2>=25?'#fd7e14':'#dc3545')+'"></div></div>'
      +'<div style="text-align:right;font-size:.8rem;font-weight:700;color:#495057;margin-top:4px">'+pp+'% complete</div></div>';
  } else {
    ckHtml = '<div style="color:#adb5bd;font-size:.85rem;padding:10px">No circuit records found for this CLLI.</div>';
  }
  document.getElementById('lookup-circuit-body').innerHTML = ckHtml;
}

function row(label, val){
  return '<div class="info-row"><span class="info-label">'+label+'</span><span class="info-val">'+(val||'—')+'</span></div>';
}

// ── Notes / Flag write-back ───────────────────────────────────────────────────
let _notesClli = '';
const SERVER = 'http://localhost:5000';

function loadNote(clli){
  _notesClli = clli;
  document.getElementById('notes-clli-label').textContent = clli;
  document.getElementById('notes-text').value = '';
  document.getElementById('notes-updated').textContent = '';
  document.getElementById('flag-check').checked = false;
  document.getElementById('flag-label').className = 'flag-toggle';
  document.getElementById('notes-saved-msg').style.display = 'none';

  fetch(SERVER+'/api/notes/'+clli)
    .then(r => r.json())
    .then(d => {
      document.getElementById('notes-text').value = d.note || '';
      document.getElementById('flag-check').checked = !!d.flagged;
      document.getElementById('flag-label').className = 'flag-toggle' + (d.flagged?' flagged':'');
      if(d.updated) document.getElementById('notes-updated').textContent = 'Last saved: '+d.updated.slice(0,16);
      document.getElementById('lookup-notes-wrap').style.display = 'block';
      document.getElementById('notes-server-warn').style.display = 'none';
    })
    .catch(() => {
      document.getElementById('lookup-notes-wrap').style.display = 'none';
      document.getElementById('notes-server-warn').style.display = 'block';
    });
}

function saveNote(){
  if(!_notesClli) return;
  const note    = document.getElementById('notes-text').value.trim();
  const flagged = document.getElementById('flag-check').checked;
  document.getElementById('flag-label').className = 'flag-toggle'+(flagged?' flagged':'');
  fetch(SERVER+'/api/notes/'+_notesClli, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({note, flagged})
  })
  .then(r => r.json())
  .then(() => {
    const msg = document.getElementById('notes-saved-msg');
    msg.style.display = 'inline';
    document.getElementById('notes-updated').textContent = 'Last saved: '+new Date().toISOString().slice(0,16).replace('T',' ');
    setTimeout(()=>msg.style.display='none', 2500);
  })
  .catch(() => alert('Could not save — is the server running?'));
}

// ── Velocity & Risk ──────────────────────────────────────────────────────────
function initVelocity(){
  const VS = DATA.vel_summary;
  const VL = DATA.velocity;
  const RK = DATA.risk;

  document.getElementById('vel-kpi-total').textContent   = fmt(VS.total_completed);
  document.getElementById('vel-kpi-last').textContent    = fmt(VS.last_month);
  document.getElementById('vel-kpi-last-sub').textContent= VS.last_month_ym || '—';
  document.getElementById('vel-kpi-avg').textContent     = fmt(VS.avg3);
  document.getElementById('vel-kpi-proj').textContent    = VS.months_to_complete ? fmt(VS.months_to_complete) : '—';

  // Monthly completions bar + rolling avg line
  const labels = VL.YM;
  const counts = VL.COMPLETIONS;
  // 3-month rolling avg
  const rollingAvg = counts.map((_,i) => {
    const slice = counts.slice(Math.max(0,i-2), i+1);
    return +(slice.reduce((a,b)=>a+b,0)/slice.length).toFixed(0);
  });

  Plotly.newPlot('chart-velocity', [
    {
      name:'Monthly Completions', type:'bar', x:labels, y:counts,
      marker:{color: labels.map(ym => ym >= '2025-08' ? '#198754' : '#dee2e6')},
      hovertemplate:'%{x}: %{y:,} circuits<extra></extra>'
    },
    {
      name:'3-Mo Rolling Avg', type:'scatter', mode:'lines+markers',
      x:labels, y:rollingAvg,
      line:{color:'#0d6efd', width:2}, marker:{size:4},
      hovertemplate:'%{x} avg: %{y:,}<extra></extra>'
    }
  ], {
    barmode:'overlay',
    margin:{t:10,b:80,l:70,r:20},
    xaxis:{tickangle:-45, title:'Month'},
    yaxis:{tickformat:',', title:'Circuits Completed'},
    legend:{orientation:'h', y:-0.25},
    shapes:[{
      type:'line', x0:'2025-08', x1:'2025-08',
      y0:0, y1:1, yref:'paper',
      line:{color:'#fd7e14', dash:'dot', width:1.5}
    }],
    annotations:[{
      x:'2025-08', y:1, yref:'paper', xanchor:'left',
      text:'Ramp-up', showarrow:false,
      font:{size:11, color:'#fd7e14'}
    }]
  }, {responsive:true, displayModeBar:false});

  // Risk donut
  const riskColors = {
    'High Risk':'#dc3545','Medium Risk':'#fd7e14',
    'Lower Risk':'#198754','Complete':'#0d6efd','No Date Set':'#adb5bd'
  };
  const riskCnt = {};
  RK.RISK.forEach(r=>{ riskCnt[r]=(riskCnt[r]||0)+1; });
  const rKeys = Object.keys(riskCnt).sort();
  Plotly.newPlot('chart-risk-donut', [{
    type:'pie', hole:.45,
    labels:rKeys, values:rKeys.map(k=>riskCnt[k]),
    marker:{colors:rKeys.map(k=>riskColors[k]||'#6c757d')},
    textinfo:'label+value', hovertemplate:'%{label}: %{value}<extra></extra>'
  }], {margin:{t:10,b:10,l:10,r:10}, showlegend:true, legend:{orientation:'h',y:-0.12}},
  {responsive:true, displayModeBar:false});

  // Remaining circuits by risk level
  const riskRem = {};
  RK.RISK.forEach((r,i)=>{ riskRem[r]=(riskRem[r]||0)+(RK.REMAINING[i]||0); });
  const rrKeys = ['High Risk','Medium Risk','Lower Risk','No Date Set'].filter(k=>riskRem[k]);
  Plotly.newPlot('chart-risk-bar', [{
    type:'bar', orientation:'h',
    y:rrKeys.slice().reverse(), x:rrKeys.map(k=>riskRem[k]).slice().reverse(),
    marker:{color:rrKeys.map(k=>riskColors[k]).slice().reverse()},
    hovertemplate:'%{y}: %{x:,} circuits remaining<extra></extra>'
  }], {margin:{t:10,b:40,l:120,r:60}, xaxis:{tickformat:','}},
  {responsive:true, displayModeBar:false});

  // Populate filter dropdowns
  const riskEl  = document.getElementById('risk-filter-risk');
  const stateEl = document.getElementById('risk-filter-state');
  const statEl  = document.getElementById('risk-filter-status');
  ['High Risk','Medium Risk','Lower Risk','Complete','No Date Set'].forEach(v=>{
    const o=document.createElement('option'); o.value=v; o.textContent=v; riskEl.appendChild(o); });
  [...new Set(RK.STATE)].filter(Boolean).sort().forEach(v=>{
    const o=document.createElement('option'); o.value=v; o.textContent=v; stateEl.appendChild(o); });
  [...new Set(RK.SWITCH_STATUS)].filter(Boolean).sort().forEach(v=>{
    const o=document.createElement('option'); o.value=v; o.textContent=v; statEl.appendChild(o); });

  renderRiskTable();
}

function renderRiskTable(){
  const RK     = DATA.risk;
  const fRisk  = document.getElementById('risk-filter-risk').value;
  const fState = document.getElementById('risk-filter-state').value;
  const fStat  = document.getElementById('risk-filter-status').value;
  const fSrch  = document.getElementById('risk-search').value.trim().toUpperCase();

  const riskBadge = r =>
    r==='High Risk'   ? '<span class="badge" style="background:#f8d7da;color:#58151c">High Risk</span>' :
    r==='Medium Risk' ? '<span class="badge" style="background:#fff3cd;color:#664d03">Medium Risk</span>' :
    r==='Lower Risk'  ? '<span class="badge" style="background:#d1e7dd;color:#0a3622">Lower Risk</span>' :
    r==='Complete'    ? '<span class="badge" style="background:#cfe2ff;color:#084298">Complete</span>' :
                        '<span class="badge" style="background:#e9ecef;color:#495057">No Date</span>';
  const statusBadge = s =>
    s==='Remove' ? '<span class="badge badge-remove">Remove</span>' :
    s==='AIP'    ? '<span class="badge badge-aip">AIP</span>' : s||'—';

  let html = '';
  for(let i=0; i<RK.CLLI.length; i++){
    if(fRisk  && RK.RISK[i]  !== fRisk)  continue;
    if(fState && RK.STATE[i] !== fState) continue;
    if(fStat  && RK.SWITCH_STATUS[i] !== fStat) continue;
    if(fSrch  && !(RK.CLLI[i]||'').includes(fSrch) &&
                 !(RK.WIRE_CENTER[i]||'').toUpperCase().includes(fSrch)) continue;
    const tot = RK.TOTAL[i]||0;
    const rem = RK.REMAINING[i]||0;
    const pp  = RK.PCT_DONE[i]||0;
    html += '<tr onclick="lookupCLLI(this.dataset.clli)" data-clli="'+RK.CLLI[i]+'" style="cursor:pointer">'
      +'<td style="color:#0d6efd;font-weight:600;text-decoration:underline">'+(RK.CLLI[i]||'—')+'</td>'
      +'<td>'+(RK.WIRE_CENTER[i]||'—')+'</td>'
      +'<td>'+(RK.STATE[i]||'—')+'</td>'
      +'<td>'+(RK.SWITCH_TYPE[i]||'—')+'</td>'
      +'<td>'+statusBadge(RK.SWITCH_STATUS[i])+'</td>'
      +'<td>'+(RK.SWITCH_CUTOVER_DATE[i]||RK.CUTOVER_YEAR[i]||'—')+'</td>'
      +'<td data-val="'+tot+'">'+fmt(tot)+'</td>'
      +'<td data-val="'+(RK.COMPLETED[i]||0)+'">'+fmt(RK.COMPLETED[i]||0)+'</td>'
      +'<td data-val="'+rem+'">'+fmt(rem)+'</td>'
      +'<td data-val="'+pp+'">'
        +'<div style="display:flex;align-items:center;gap:6px">'
        +'<div class="pbar"><div class="pbar-fill" style="width:'+Math.min(pp,100)+'%;background:'+(pp>=75?'#198754':pp>=25?'#fd7e14':'#dc3545')+'"></div></div>'
        +'<span style="font-size:.75rem;min-width:36px">'+pp+'%</span>'
        +'</div></td>'
      +'<td>'+riskBadge(RK.RISK[i])+'</td>'
      +'</tr>';
  }
  document.getElementById('risk-tbl-body').innerHTML = html ||
    '<tr><td colspan="11" style="text-align:center;color:#adb5bd;padding:20px">No records match filters</td></tr>';
}

// ── Program KPIs ─────────────────────────────────────────────────────────────
function initKPIs(){
  const S  = DATA.summary;
  const VS = DATA.vel_summary;
  const VL = DATA.velocity;
  const SV = DATA.savings;

  // Program health KPIs
  document.getElementById('kpi2-cllis').textContent    = fmt(S.total_cllis);
  document.getElementById('kpi2-sw').textContent       = fmt(S.total_switches);
  document.getElementById('kpi2-tot').textContent      = fmt(S.total_circuits);
  document.getElementById('kpi2-done').textContent     = fmt(S.completed);
  document.getElementById('kpi2-done-sub').textContent = pct(S.completed, S.total_circuits) + ' complete';
  document.getElementById('kpi2-rem').textContent      = fmt(S.pending + S.in_progress);
  document.getElementById('kpi2-pace').textContent     = fmt(VS.avg3);
  document.getElementById('kpi2-proj').textContent     = VS.months_to_complete ? fmt(VS.months_to_complete) : '—';
  document.getElementById('kpi2-lastmo').textContent   = fmt(VS.last_month);
  document.getElementById('kpi2-lastmo-sub').textContent = 'circuits in ' + (VS.last_month_ym || '—');

  // Savings KPIs
  const fmtDollars = v => v ? '$' + Number(v).toLocaleString(undefined,{maximumFractionDigits:0}) : '—';
  const fmtFt      = v => v ? Number(v).toLocaleString() + ' ft' : '—';
  document.getElementById('kpi2-annual').textContent  = fmtDollars(SV.total_annual);
  document.getElementById('kpi2-actual').textContent  = fmtDollars(SV.total_actual);
  document.getElementById('kpi2-copper').textContent  = fmtFt(SV.copper_footage);
  document.getElementById('kpi2-oe').textContent      = fmt(SV.analog_oe);
  document.getElementById('kpi2-sav-note').textContent =
    'Note: savings data available for ' + SV.savings_populated + ' of ' + SV.total_switches + ' switches.';

  // Site activity — dual axis bar + line
  const labels = VL.YM;
  const comps  = VL.COMPLETIONS;
  const sites  = VL.ACTIVE_SITES;
  const rolling = comps.map((_,i)=>{
    const sl = comps.slice(Math.max(0,i-2),i+1);
    return +(sl.reduce((a,b)=>a+b,0)/sl.length).toFixed(0);
  });

  Plotly.newPlot('chart-kpi-activity', [
    {
      name:'Circuits Completed', type:'bar', x:labels, y:comps,
      marker:{color: labels.map(ym => ym >= '2025-08' ? '#0d6efd' : '#dee2e6')},
      hovertemplate:'%{x}<br>Circuits: %{y:,}<extra></extra>',
      yaxis:'y'
    },
    {
      name:'3-Mo Rolling Avg', type:'scatter', mode:'lines', x:labels, y:rolling,
      line:{color:'#6f42c1', width:2, dash:'dot'},
      hovertemplate:'%{x} avg: %{y:,}<extra></extra>',
      yaxis:'y'
    },
    {
      name:'Active CLLIs', type:'scatter', mode:'lines+markers', x:labels, y:sites,
      line:{color:'#fd7e14', width:2}, marker:{size:5},
      hovertemplate:'%{x}<br>Active CLLIs: %{y:,}<extra></extra>',
      yaxis:'y2'
    }
  ], {
    margin:{t:10,b:80,l:70,r:70},
    xaxis:{tickangle:-45},
    yaxis: {title:'Circuits Completed', tickformat:',', titlefont:{color:'#0d6efd'}, tickfont:{color:'#0d6efd'}},
    yaxis2:{title:'Active CLLIs', overlaying:'y', side:'right', titlefont:{color:'#fd7e14'}, tickfont:{color:'#fd7e14'}},
    legend:{orientation:'h', y:-0.28},
    shapes:[{type:'line',x0:'2025-08',x1:'2025-08',y0:0,y1:1,yref:'paper',
             line:{color:'#198754',dash:'dot',width:1.5}}],
    annotations:[{x:'2025-08',y:1,yref:'paper',xanchor:'left',
                  text:'Ramp-up',showarrow:false,font:{size:11,color:'#198754'}}]
  }, {responsive:true, displayModeBar:false});

  // Savings by switch status — donut
  Plotly.newPlot('chart-kpi-sav-status', [{
    type:'pie', hole:.45,
    labels: SV.by_status_labels,
    values: SV.by_status_annual,
    text:   SV.by_status_count.map(c=>'n='+c),
    textinfo:'label+percent',
    hovertemplate:'%{label}<br>Annual savings: $%{value:,.0f}<br>%{text}<extra></extra>'
  }], {margin:{t:10,b:10,l:10,r:10}, showlegend:true, legend:{orientation:'h',y:-0.12}},
  {responsive:true, displayModeBar:false});

  // Copper footage by state
  const topStates = SV.state_labels.slice(0,15);
  const topCopper = SV.state_copper.slice(0,15);
  Plotly.newPlot('chart-kpi-copper', [{
    type:'bar', orientation:'h',
    y: topStates.slice().reverse(),
    x: topCopper.slice().reverse(),
    marker:{color:'#fd7e14'},
    hovertemplate:'%{y}: %{x:,} ft<extra></extra>'
  }], {margin:{t:10,b:40,l:60,r:80}, xaxis:{tickformat:',', title:'Feet'}, yaxis:{automargin:true}},
  {responsive:true, displayModeBar:false});

  // Analog OE by state — sort by analog_oe
  const oeIdx   = SV.state_labels.map((_,i)=>i).sort((a,b)=>SV.state_analog[b]-SV.state_analog[a]).slice(0,15);
  const oeLabel = oeIdx.map(i=>SV.state_labels[i]);
  const oeVal   = oeIdx.map(i=>SV.state_analog[i]);
  Plotly.newPlot('chart-kpi-oe', [{
    type:'bar', orientation:'h',
    y: oeLabel.slice().reverse(),
    x: oeVal.slice().reverse(),
    marker:{color:'#6f42c1'},
    hovertemplate:'%{y}: %{x:,} lines<extra></extra>'
  }], {margin:{t:10,b:40,l:60,r:60}, xaxis:{tickformat:',', title:'Lines'}, yaxis:{automargin:true}},
  {responsive:true, displayModeBar:false});
}

// ── Dependencies ──────────────────────────────────────────────────────────────
let _depInited = false;
function initDependencies(){
  if(_depInited) return;
  _depInited = true;

  const DS = DATA.dep_summary;
  const DD = DATA.deps;
  const noDepCount = DD.CLLI.filter((_,i)=>DD.DEP_COUNT[i]===0).length;

  document.getElementById('dep-kpi-count').textContent = fmt(DS.switches_with_deps);
  document.getElementById('dep-kpi-wcs').textContent   = fmt(DS.total_dep_wcs);
  document.getElementById('dep-kpi-max').textContent   = fmt(DS.max_deps);
  document.getElementById('dep-kpi-none').textContent  = fmt(noDepCount);

  // Populate filters
  const regions = [...new Set(DD.REGION.filter(Boolean))].sort();
  const states  = [...new Set(DD.STATE.filter(Boolean))].sort();
  const rSel = document.getElementById('dep-filter-region');
  const sSel = document.getElementById('dep-filter-state');
  regions.forEach(r=>{ const o=document.createElement('option'); o.value=r; o.textContent=r; rSel.appendChild(o); });
  states.forEach(s=>{  const o=document.createElement('option'); o.value=s; o.textContent=s; sSel.appendChild(o); });

  // Top 20 bar chart (horizontal)
  const top20 = DD.CLLI
    .map((c,i)=>({clli:c, cnt:DD.DEP_COUNT[i]}))
    .filter(d=>d.cnt>0)
    .sort((a,b)=>b.cnt-a.cnt)
    .slice(0,20);
  Plotly.newPlot('dep-bar-chart',[{
    type:'bar', orientation:'h',
    y: top20.map(d=>d.clli).reverse(),
    x: top20.map(d=>d.cnt).reverse(),
    marker:{color:'#dc3545'},
    text: top20.map(d=>String(d.cnt)).reverse(),
    textposition:'outside',
    hovertemplate:'%{y}: %{x} dependent WCs<extra></extra>'
  }],{
    margin:{l:100,r:40,t:20,b:40},
    xaxis:{title:'# Dependent Wire Centers'},
    yaxis:{automargin:true},
    paper_bgcolor:'transparent', plot_bgcolor:'transparent'
  },{responsive:true, displayModeBar:false});

  // Donut: dependency depth buckets
  const buckets = {'0 (No Dependents)':0,'1-5':0,'6-10':0,'11-20':0,'21+':0};
  DD.DEP_COUNT.forEach(n=>{
    if(n===0)       buckets['0 (No Dependents)']++;
    else if(n<=5)   buckets['1-5']++;
    else if(n<=10)  buckets['6-10']++;
    else if(n<=20)  buckets['11-20']++;
    else            buckets['21+']++;
  });
  Plotly.newPlot('dep-donut-chart',[{
    type:'pie', hole:0.45,
    labels: Object.keys(buckets),
    values: Object.values(buckets),
    marker:{colors:['#adb5bd','#0d6efd','#ffc107','#fd7e14','#dc3545']},
    textinfo:'label+value',
    hovertemplate:'%{label}: %{value} switches<extra></extra>'
  }],{
    margin:{l:20,r:20,t:20,b:20},
    showlegend:true,legend:{orientation:'h',y:-0.12},
    paper_bgcolor:'transparent', plot_bgcolor:'transparent'
  },{responsive:true, displayModeBar:false});

  renderDepTable();
}

let _depSort = {col:7, dir:-1};
function renderDepTable(){
  const DD    = DATA.deps;
  const reg   = document.getElementById('dep-filter-region').value;
  const st    = document.getElementById('dep-filter-state').value;
  const dFilt = document.getElementById('dep-filter-deps').value;
  const q     = document.getElementById('dep-search').value.trim().toUpperCase();

  const riskColor = {
    'High Risk':'#dc3545','Medium Risk':'#fd7e14','Lower Risk':'#198754',
    'No Date Set':'#6c757d','Complete':'#0d6efd'
  };

  let rows = [];
  for(let i=0; i<DD.CLLI.length; i++){
    if(reg   && DD.REGION[i]!==reg)  continue;
    if(st    && DD.STATE[i]!==st)    continue;
    if(dFilt==='yes' && DD.DEP_COUNT[i]===0)  continue;
    if(dFilt==='no'  && DD.DEP_COUNT[i]>0)    continue;
    if(q && !DD.CLLI[i].includes(q)) continue;
    rows.push(i);
  }

  document.getElementById('dep-tbl-count').textContent = rows.length+' switches';

  const rk = _depSort.col, rd = _depSort.dir;
  const getVal = (idx,col) => [
    DD.CLLI[idx], DD.STATE[idx], DD.REGION[idx], DD.SWITCH_TYPE[idx],
    DD.PCT_DONE[idx]||0, DD.RISK[idx], DD.UPSTREAM[idx],
    DD.DEP_COUNT[idx]||0, DD.DEP_LIST[idx]
  ][col];
  rows.sort((a,b)=>{
    const va=getVal(a,rk), vb=getVal(b,rk);
    if(va<vb) return -1*rd; if(va>vb) return 1*rd; return 0;
  });

  let html = '';
  rows.forEach(i=>{
    const rc = riskColor[DD.RISK[i]]||'#6c757d';
    const depBadge = DD.DEP_COUNT[i]>0
      ? '<span style="background:#dc3545;color:#fff;border-radius:10px;padding:1px 7px;font-size:.75rem">'+DD.DEP_COUNT[i]+'</span>'
      : '<span style="color:#adb5bd">0</span>';
    const depList = DD.DEP_LIST[i]
      ? '<span style="font-size:.72rem;color:#495057">'+DD.DEP_LIST[i]+'</span>'
      : '<span style="color:#adb5bd;font-size:.8rem">—</span>';
    const upstream = DD.UPSTREAM[i]||'—';
    html += '<tr onclick="lookupCLLI(this.dataset.clli)" data-clli="'+DD.CLLI[i]+'" style="cursor:pointer">'
      +'<td><strong>'+DD.CLLI[i]+'</strong></td>'
      +'<td>'+DD.STATE[i]+'</td>'
      +'<td>'+DD.REGION[i]+'</td>'
      +'<td>'+DD.SWITCH_TYPE[i]+'</td>'
      +'<td>'+(DD.PCT_DONE[i]||0).toFixed(1)+'%</td>'
      +'<td><span style="color:'+rc+';font-weight:600">'+DD.RISK[i]+'</span></td>'
      +'<td style="font-size:.8rem">'+upstream+'</td>'
      +'<td style="text-align:center">'+depBadge+'</td>'
      +'<td>'+depList+'</td>'
      +'</tr>';
  });
  document.getElementById('dep-tbl-body').innerHTML = html || '<tr><td colspan="9" style="text-align:center;color:#adb5bd;padding:20px">No matches</td></tr>';
  document.querySelectorAll('#dep-tbl th').forEach((th,i)=>{
    th.style.cursor='pointer';
    th.onclick=()=>{ _depSort=(_depSort.col===i)?{col:i,dir:-_depSort.dir}:{col:i,dir:1}; renderDepTable(); };
  });
}

// ── Flagged Sites ─────────────────────────────────────────────────────────────
let _flaggedRows = [];

function loadFlaggedSites(){
  const loadEl    = document.getElementById('fl-loading');
  const offlineEl = document.getElementById('fl-offline');
  const contentEl = document.getElementById('fl-content');
  loadEl.style.display = 'block';
  offlineEl.style.display = 'none';
  contentEl.style.display = 'none';

  fetch(SERVER + '/api/notes')
    .then(r => r.json())
    .then(data => {
      loadEl.style.display = 'none';
      if(!Array.isArray(data) || data.length === 0){
        _flaggedRows = [];
        contentEl.style.display = 'block';
        document.getElementById('fl-kpi-flagged').textContent = '0';
        document.getElementById('fl-kpi-notes').textContent   = '0';
        document.getElementById('fl-kpi-total').textContent   = '0';
        document.getElementById('fl-kpi-latest').textContent  = '—';
        renderFlaggedTable();
        return;
      }

      // Enrich with circuit/switch data
      const CK = DATA.circuits;
      const SW = DATA.switches;
      _flaggedRows = data.map(d => {
        const clli = (d.clli||'').toUpperCase();
        const ci = CK.CLLI_CD.indexOf(clli);
        const si = SW.CLLI.indexOf(clli);
        return {
          clli:      clli,
          note:      d.note || '',
          flagged:   d.flagged,
          updated:   d.updated ? d.updated.slice(0,16).replace('T',' ') : '',
          wc:        ci>=0 ? CK.WC_NAME[ci]    : (si>=0 ? SW.WIRE_CENTER[si] : ''),
          region:    ci>=0 ? CK.REGION[ci]      : (si>=0 ? SW.REGION[si]      : ''),
          state:     ci>=0 ? CK.STATE[ci]       : (si>=0 ? SW.STATE[si]       : ''),
          sw_type:   si>=0 ? SW.SWITCH_TYPE[si] : (ci>=0 ? CK.SWITCH_TYPE[ci] : ''),
          total:     ci>=0 ? (CK.TOTAL[ci]||0)     : 0,
          completed: ci>=0 ? (CK.COMPLETED[ci]||0) : 0,
        };
      });

      const flaggedCnt = _flaggedRows.filter(r=>r.flagged).length;
      const notesCnt   = _flaggedRows.filter(r=>r.note.trim()!=='').length;
      const latest     = _flaggedRows.map(r=>r.updated).filter(Boolean).sort().slice(-1)[0] || '—';
      document.getElementById('fl-kpi-flagged').textContent = flaggedCnt;
      document.getElementById('fl-kpi-notes').textContent   = notesCnt;
      document.getElementById('fl-kpi-total').textContent   = _flaggedRows.length;
      document.getElementById('fl-kpi-latest').textContent  = latest;

      // Populate region/state filters
      const regEl = document.getElementById('fl-filter-region');
      const stEl  = document.getElementById('fl-filter-state');
      regEl.innerHTML = '<option value="">All</option>';
      stEl.innerHTML  = '<option value="">All</option>';
      [...new Set(_flaggedRows.map(r=>r.region))].filter(Boolean).sort()
        .forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; regEl.appendChild(o); });
      [...new Set(_flaggedRows.map(r=>r.state))].filter(Boolean).sort()
        .forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; stEl.appendChild(o); });

      contentEl.style.display = 'block';
      renderFlaggedTable();
    })
    .catch(() => {
      loadEl.style.display    = 'none';
      offlineEl.style.display = 'block';
    });
}

function renderFlaggedTable(){
  const fShow   = document.getElementById('fl-filter-show').value;
  const fRegion = document.getElementById('fl-filter-region').value;
  const fState  = document.getElementById('fl-filter-state').value;
  const fSearch = document.getElementById('fl-search').value.trim().toUpperCase();

  const filtered = _flaggedRows.filter(r => {
    if(fShow === 'flagged' && !r.flagged)          return false;
    if(fShow === 'notes'   && !r.note.trim())       return false;
    if(fRegion && r.region !== fRegion)             return false;
    if(fState  && r.state  !== fState)              return false;
    if(fSearch && !r.clli.includes(fSearch) &&
                  !(r.wc||'').toUpperCase().includes(fSearch) &&
                  !(r.note||'').toUpperCase().includes(fSearch)) return false;
    return true;
  });

  document.getElementById('fl-tbl-count').textContent =
    filtered.length + ' entries' + (filtered.length < _flaggedRows.length ? ' (filtered from ' + _flaggedRows.length + ')' : '');

  let html = '';
  filtered.forEach(r => {
    const pp   = r.total ? (r.completed / r.total * 100).toFixed(1) : '0.0';
    const note = r.note.length > 80 ? r.note.slice(0,80) + '…' : r.note;
    html += '<tr onclick="lookupCLLI(this.dataset.clli)" data-clli="'+r.clli+'" style="cursor:pointer">'
      + '<td style="color:#0d6efd;font-weight:600;text-decoration:underline">'+r.clli+'</td>'
      + '<td>'+(r.wc||'—')+'</td>'
      + '<td>'+(r.region||'—')+'</td>'
      + '<td>'+(r.state||'—')+'</td>'
      + '<td>'+(r.sw_type||'—')+'</td>'
      + '<td data-val="'+r.total+'">'+fmt(r.total)+'</td>'
      + '<td data-val="'+pp+'">'
          + '<div style="display:flex;align-items:center;gap:6px">'
          + '<div class="pbar"><div class="pbar-fill" style="width:'+Math.min(parseFloat(pp),100)+'%;background:'+(parseFloat(pp)>=75?'#198754':parseFloat(pp)>=25?'#fd7e14':'#dc3545')+'"></div></div>'
          + '<span style="font-size:.75rem;min-width:36px">'+pp+'%</span>'
          + '</div></td>'
      + '<td>'+(r.flagged ? '<span class="badge" style="background:#fff3cd;color:#664d03">&#9873; Flagged</span>' : '—')+'</td>'
      + '<td style="font-size:.78rem;color:#495057;max-width:320px" title="'+r.note.replace(/"/g,"&quot;")+'">'+note+'</td>'
      + '<td style="font-size:.76rem;color:#868e96">'+r.updated+'</td>'
      + '</tr>';
  });

  document.getElementById('fl-tbl-body').innerHTML = html ||
    '<tr><td colspan="10" style="text-align:center;color:#adb5bd;padding:24px">'
    + ((_flaggedRows.length === 0)
      ? 'No notes or flags saved yet. Add them from the Site Lookup tab.'
      : 'No entries match the current filters.')
    + '</td></tr>';
}

// ── Lookup Dependencies ───────────────────────────────────────────────────────
function loadLookupDeps(clli){
  const DD   = DATA.deps;
  const wrap = document.getElementById('lookup-dep-wrap');
  const idx  = DD.CLLI.indexOf(clli);
  if(idx === -1){ wrap.style.display='none'; return; }

  document.getElementById('lookup-dep-clli-label').textContent = clli;
  wrap.style.display = 'block';

  const upstream = DD.UPSTREAM[idx] || '—';
  const depCount = DD.DEP_COUNT[idx] || 0;
  const depList  = DD.DEP_LIST[idx]  || '';

  let html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">';

  // Upstream panel
  html += '<div><div style="font-weight:700;font-size:.85rem;color:#495057;margin-bottom:6px">Upstream Switch(es)</div>';
  if(upstream === '—'){
    html += '<div style="color:#adb5bd;font-size:.85rem">No upstream dependency recorded</div>';
  } else {
    upstream.split(',').map(s=>s.trim()).forEach(sw=>{
      html += '<span style="display:inline-block;background:#e3f2fd;color:#0d6efd;border-radius:4px;padding:3px 10px;margin:2px;font-size:.82rem;font-weight:600;cursor:pointer" onclick="lookupCLLI(\''+sw+'\')">'+sw+'</span>';
    });
    html += '<div style="font-size:.75rem;color:#6c757d;margin-top:4px">Click to look up any upstream switch</div>';
  }
  html += '</div>';

  // Dependents panel
  html += '<div><div style="font-weight:700;font-size:.85rem;color:#495057;margin-bottom:6px">Wire Centers Depending on This Switch <span style="background:#dc3545;color:#fff;border-radius:10px;padding:1px 8px;font-size:.75rem;margin-left:4px">'+depCount+'</span></div>';
  if(depCount === 0){
    html += '<div style="color:#adb5bd;font-size:.85rem">No wire centers route through this switch</div>';
  } else {
    depList.split(',').map(s=>s.trim()).forEach(wc=>{
      const isDecom = DATA.deps.CLLI.includes(wc);
      const bg = isDecom ? '#fff3cd' : '#f8f9fa';
      const co = isDecom ? '#856404' : '#495057';
      html += '<span style="display:inline-block;background:'+bg+';color:'+co+';border-radius:4px;padding:3px 10px;margin:2px;font-size:.82rem;font-weight:600'+(isDecom?';cursor:pointer\' onclick=\'lookupCLLI("'+wc+'")\'' :'\'')+'">'+wc+(isDecom?' &#9888;':'')+'</span>';
    });
    html += '<div style="font-size:.75rem;color:#6c757d;margin-top:6px">&#9888; = also a decom switch &nbsp;|&nbsp; These WCs must be rehomed before this switch can be decommissioned</div>';
  }
  html += '</div></div>';

  document.getElementById('lookup-dep-body').innerHTML = html;
}

// ── Lookup Device Inventory ───────────────────────────────────────────────────
function loadLookupDevices(clli){
  const DV   = DATA.devices;
  const wrap = document.getElementById('lookup-devices-wrap');
  const idxs = [];
  for(let i=0; i<DV.BUILDING_CLLI.length; i++){
    if(DV.BUILDING_CLLI[i] === clli) idxs.push(i);
  }
  if(idxs.length === 0){ wrap.style.display='none'; return; }

  document.getElementById('lookup-dev-clli-label').textContent = clli;
  wrap.style.display = 'block';

  const totalCkts = idxs.reduce((s,i)=>s+(DV.CKT_COUNT[i]||0), 0);
  const pwrDown   = idxs.filter(i=>DV.DEVICE_POWERED_DOWN[i]==='Y').length;
  const pwrSav    = idxs.reduce((s,i)=>s+(+DV.POWER_SAVINGS[i]||0), 0);
  document.getElementById('lu-dev-count').textContent = fmt(idxs.length);
  document.getElementById('lu-dev-ckts').textContent  = fmt(totalCkts);
  document.getElementById('lu-dev-pwrdn').textContent = fmt(pwrDown);
  document.getElementById('lu-dev-pwr').textContent   = fmt(pwrSav);

  const utilColor = u =>
    u==='ZERO-FILL' ? 'color:#198754;font-weight:600' :
    u==='LOW-FILL'  ? 'color:#fd7e14;font-weight:600' :
    u==='WORKING'   ? 'color:#0d6efd;font-weight:600' : '';
  const yesNo = v =>
    v==='Y' ? '<span class="badge badge-y">Yes</span>' :
    v==='N' ? '<span class="badge badge-n">No</span>' : '—';

  let html = '';
  idxs.forEach(i => {
    const pwr = DV.POWER_SAVINGS[i];
    html += '<tr>'
      + '<td>' + (DV.DEVICE_NAME[i]||'—') + '</td>'
      + '<td>' + (DV.DEVICE_TYPE[i]||'—') + '</td>'
      + '<td data-val="' + (DV.CKT_COUNT[i]||0) + '">' + fmt(DV.CKT_COUNT[i]||0) + '</td>'
      + '<td style="' + utilColor(DV.DEVICE_UTILIZATION[i]) + '">' + (DV.DEVICE_UTILIZATION[i]||'—') + '</td>'
      + '<td>' + yesNo(DV.DEVICE_POWERED_DOWN[i]) + '</td>'
      + '<td>' + yesNo(DV.DEVICE_REMOVED[i]) + '</td>'
      + '<td style="font-size:.75rem">' + (DV.CURRENT_MILESTONE[i]||'—') + '</td>'
      + '<td>' + (DV.CURRENT_MILESTONE_STATUS[i]||'—') + '</td>'
      + '<td>' + (DV.MILESTONE_START[i]||'—') + '</td>'
      + '<td data-val="' + (pwr||0) + '">' + (pwr ? fmt(pwr) : '—') + '</td>'
      + '</tr>';
  });
  document.getElementById('lu-dev-tbl-body').innerHTML = html;
}

// ── Circuit Detail (live drill-down via Flask) ────────────────────────────────
let _cktDetRows = [], _cktDetFiltered = [], _cktDetPage = 0;
const CKT_DET_PER = 50;

function loadCircuits(clli){
  const wrap    = document.getElementById('ckt-detail-wrap');
  const loading = document.getElementById('ckt-detail-loading');
  const content = document.getElementById('ckt-detail-content');
  document.getElementById('ckt-detail-title').textContent = 'Circuit Detail — ' + clli;
  wrap.style.display = 'block';
  loading.style.display = 'block';
  content.style.display = 'none';
  _cktDetRows = []; _cktDetPage = 0;

  fetch(SERVER + '/api/circuits/' + clli)
    .then(r => r.json())
    .then(d => {
      if(d.error){ loading.textContent = 'Error loading circuits: ' + d.error; return; }
      _cktDetRows = d.rows;
      // populate type filter
      const typeEl = document.getElementById('ckt-det-filter-type');
      typeEl.innerHTML = '<option value="">All Types</option>';
      [...new Set(d.rows.map(r => r[8]))].filter(Boolean).sort()
        .forEach(t => { const o = document.createElement('option'); o.value=t; o.textContent=t; typeEl.appendChild(o); });
      document.getElementById('ckt-det-filter-status').value = '';
      document.getElementById('ckt-det-search').value = '';
      loading.style.display = 'none';
      content.style.display = 'block';
      renderCktDetail();
    })
    .catch(() => { wrap.style.display = 'none'; });
}

function renderCktDetail(){
  const fStatus = document.getElementById('ckt-det-filter-status').value;
  const fType   = document.getElementById('ckt-det-filter-type').value;
  const fSearch = document.getElementById('ckt-det-search').value.trim().toUpperCase();
  _cktDetFiltered = _cktDetRows.filter(r => {
    if(fStatus && r[3] !== fStatus) return false;
    if(fType   && r[8] !== fType)   return false;
    if(fSearch && !(r[0]||'').toUpperCase().includes(fSearch) &&
                  !(r[7]||'').toUpperCase().includes(fSearch)) return false;
    return true;
  });
  _cktDetPage = 0;
  renderCktDetPage();
}

function cktDetPage(dir){
  const maxPage = Math.max(0, Math.ceil(_cktDetFiltered.length / CKT_DET_PER) - 1);
  _cktDetPage = Math.max(0, Math.min(_cktDetPage + dir, maxPage));
  renderCktDetPage();
}

function renderCktDetPage(){
  const start = _cktDetPage * CKT_DET_PER;
  const slice = _cktDetFiltered.slice(start, start + CKT_DET_PER);
  const totalPages = Math.max(1, Math.ceil(_cktDetFiltered.length / CKT_DET_PER));

  document.getElementById('ckt-det-count').textContent =
    fmt(_cktDetFiltered.length) + ' circuits' +
    (_cktDetFiltered.length < _cktDetRows.length ? ' (filtered from ' + fmt(_cktDetRows.length) + ')' : '');
  document.getElementById('ckt-det-page-info').textContent =
    'Page ' + (_cktDetPage + 1) + ' of ' + totalPages;
  const prevBtn = document.getElementById('ckt-det-prev');
  const nextBtn = document.getElementById('ckt-det-next');
  prevBtn.disabled = _cktDetPage === 0;
  nextBtn.disabled = _cktDetPage >= totalPages - 1;

  const statusBadge = s =>
    s==='Y' ? '<span class="badge badge-y">Done</span>' :
    s==='P' ? '<span class="badge badge-p">In Prog</span>' :
              '<span class="badge badge-n">Pending</span>';
  const cfLabel = c =>
    c==='C' ? '<span style="color:#fd7e14;font-weight:600">Cu</span>' :
    c==='F' ? '<span style="color:#0d6efd;font-weight:600">Fi</span>' : '—';

  // row: [IDENTIFIER, USOC, USOC_DESC, STATUS, MIG_DATE, WAVE, CF, CUSTOMER, CKT_CAT]
  let html = '';
  slice.forEach(r => {
    const desc = r[2] ? (r[2].length > 42 ? r[2].slice(0,42)+'…' : r[2]) : '—';
    html += '<tr>'
      + '<td style="font-family:monospace;font-size:.74rem">' + (r[0]||'—') + '</td>'
      + '<td style="font-family:monospace;font-size:.74rem">' + (r[1]||'—') + '</td>'
      + '<td title="' + (r[2]||'') + '">' + desc + '</td>'
      + '<td>' + statusBadge(r[3]) + '</td>'
      + '<td>' + (r[4]||'—') + '</td>'
      + '<td>' + (r[5]||'—') + '</td>'
      + '<td>' + cfLabel(r[6]) + '</td>'
      + '<td style="font-size:.74rem">' + (r[7]||'—') + '</td>'
      + '</tr>';
  });
  document.getElementById('ckt-det-body').innerHTML = html ||
    '<tr><td colspan="8" style="text-align:center;color:#adb5bd;padding:16px">No circuits match filters</td></tr>';

  const pager = document.getElementById('ckt-det-pager');
  pager.style.display = totalPages > 1 ? 'flex' : 'none';
}

// ── Device Inventory ─────────────────────────────────────────────────────────
let _devFiltered = [], _devPage = 0;
const DEV_PER = 100;

function initDevices(){
  const DS = DATA.dev_summary;
  const DV = DATA.devices;

  document.getElementById('dev-kpi-cllis').textContent = fmt(DS.matched_cllis);
  document.getElementById('dev-kpi-devs').textContent  = fmt(DS.total_devices);
  document.getElementById('dev-kpi-pwrdn').textContent = fmt(DS.powered_down);
  document.getElementById('dev-kpi-pwr').textContent   = fmt(DS.power_savings_w);

  // Device type bar
  const typeCnt = {};
  DV.DEVICE_TYPE.forEach(t=>{ const k=t||'Unknown'; typeCnt[k]=(typeCnt[k]||0)+1; });
  const types = Object.keys(typeCnt).sort((a,b)=>typeCnt[b]-typeCnt[a]).slice(0,15);
  Plotly.newPlot('chart-dev-type', [{
    type:'bar', orientation:'h',
    y: types.slice().reverse(),
    x: types.map(t=>typeCnt[t]).slice().reverse(),
    marker:{color:'#6f42c1'},
    hovertemplate:'%{y}: %{x:,}<extra></extra>'
  }], {margin:{t:10,b:40,l:160,r:60}, xaxis:{tickformat:','}, yaxis:{automargin:true}},
  {responsive:true, displayModeBar:false});

  // Utilization donut
  const utilCnt = {};
  DV.DEVICE_UTILIZATION.forEach(u=>{ const k=u||'Unknown'; utilCnt[k]=(utilCnt[k]||0)+1; });
  const utilColors = {'ZERO-FILL':'#198754','LOW-FILL':'#fd7e14','WORKING':'#0d6efd','UNKNOWN':'#adb5bd','Unknown':'#adb5bd'};
  const uKeys = Object.keys(utilCnt).sort((a,b)=>utilCnt[b]-utilCnt[a]);
  Plotly.newPlot('chart-dev-util', [{
    type:'pie', hole:.45,
    labels: uKeys, values: uKeys.map(k=>utilCnt[k]),
    marker:{colors: uKeys.map(k=>utilColors[k]||'#dee2e6')},
    textinfo:'label+percent',
    hovertemplate:'%{label}: %{value:,}<extra></extra>'
  }], {margin:{t:10,b:10,l:10,r:10}, showlegend:true, legend:{orientation:'h',y:-0.12}},
  {responsive:true, displayModeBar:false});

  // Milestone status bar
  const msCnt = {};
  DV.CURRENT_MILESTONE.forEach((m,i)=>{
    if(!m) return;
    const k = DV.CURRENT_MILESTONE_STATUS[i] || 'Unknown';
    const label = m.length > 35 ? m.slice(0,35)+'…' : m;
    const combo = label + ' [' + k + ']';
    msCnt[combo] = (msCnt[combo]||0) + 1;
  });
  const msKeys = Object.keys(msCnt).sort((a,b)=>msCnt[b]-msCnt[a]).slice(0,12);
  if(msKeys.length > 0){
    Plotly.newPlot('chart-dev-milestone', [{
      type:'bar', orientation:'h',
      y: msKeys.slice().reverse(),
      x: msKeys.map(k=>msCnt[k]).slice().reverse(),
      marker:{color:'#0dcaf0'},
      hovertemplate:'%{y}: %{x:,} devices<extra></extra>'
    }], {margin:{t:10,b:40,l:340,r:60}, xaxis:{tickformat:','}, yaxis:{automargin:true}},
    {responsive:true, displayModeBar:false});
  } else {
    document.getElementById('chart-dev-milestone').innerHTML =
      '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#adb5bd;font-size:.9rem">No milestone data for matched CLLIs</div>';
  }

  // Top CLLIs by device count
  const clliCnt = {};
  DV.BUILDING_CLLI.forEach(c=>{ if(c) clliCnt[c]=(clliCnt[c]||0)+1; });
  const topCllis = Object.keys(clliCnt).sort((a,b)=>clliCnt[b]-clliCnt[a]).slice(0,20);
  Plotly.newPlot('chart-dev-clli', [{
    type:'bar', orientation:'h',
    y: topCllis.slice().reverse(),
    x: topCllis.map(c=>clliCnt[c]).slice().reverse(),
    marker:{color:'#0d6efd'},
    hovertemplate:'%{y}: %{x:,} devices<extra></extra>'
  }], {margin:{t:10,b:40,l:100,r:60}, xaxis:{tickformat:','}, yaxis:{automargin:true}},
  {responsive:true, displayModeBar:false});

  // Populate filter dropdowns
  const typeEl  = document.getElementById('dev-filter-type');
  const utilEl  = document.getElementById('dev-filter-util');
  const msEl    = document.getElementById('dev-filter-milestone');
  [...new Set(DV.DEVICE_TYPE)].filter(Boolean).sort().forEach(v=>{
    const o=document.createElement('option'); o.value=v; o.textContent=v; typeEl.appendChild(o); });
  [...new Set(DV.DEVICE_UTILIZATION)].filter(Boolean).sort().forEach(v=>{
    const o=document.createElement('option'); o.value=v; o.textContent=v; utilEl.appendChild(o); });
  [...new Set(DV.CURRENT_MILESTONE)].filter(Boolean).sort().forEach(v=>{
    const o=document.createElement('option'); o.value=v; o.textContent=v; msEl.appendChild(o); });

  renderDevTable();
}

function renderDevTable(){
  const DV    = DATA.devices;
  const fClli = document.getElementById('dev-filter-clli').value.trim().toUpperCase();
  const fType = document.getElementById('dev-filter-type').value;
  const fUtil = document.getElementById('dev-filter-util').value;
  const fPwrdn= document.getElementById('dev-filter-pwrdn').value;
  const fMs   = document.getElementById('dev-filter-milestone').value;

  _devFiltered = [];
  for(let i=0; i<DV.BUILDING_CLLI.length; i++){
    if(fClli && !(DV.BUILDING_CLLI[i]||'').includes(fClli)) continue;
    if(fType && DV.DEVICE_TYPE[i] !== fType) continue;
    if(fUtil && DV.DEVICE_UTILIZATION[i] !== fUtil) continue;
    if(fPwrdn && DV.DEVICE_POWERED_DOWN[i] !== fPwrdn) continue;
    if(fMs && DV.CURRENT_MILESTONE[i] !== fMs) continue;
    _devFiltered.push(i);
  }
  _devPage = 0;
  renderDevPage();
}

function devPage(dir){
  const maxPage = Math.max(0, Math.ceil(_devFiltered.length / DEV_PER) - 1);
  _devPage = Math.max(0, Math.min(_devPage + dir, maxPage));
  renderDevPage();
}

function renderDevPage(){
  const DV    = DATA.devices;
  const start = _devPage * DEV_PER;
  const slice = _devFiltered.slice(start, start + DEV_PER);
  const total = _devFiltered.length;
  const totalPages = Math.max(1, Math.ceil(total / DEV_PER));

  document.getElementById('dev-tbl-count').textContent =
    fmt(total) + ' records' + (total < DV.BUILDING_CLLI.length ? ' (filtered from ' + fmt(DV.BUILDING_CLLI.length) + ')' : '');
  document.getElementById('dev-page-info').textContent =
    'Page ' + (_devPage+1) + ' of ' + totalPages;
  document.getElementById('dev-prev').disabled = _devPage === 0;
  document.getElementById('dev-next').disabled = _devPage >= totalPages - 1;
  document.getElementById('dev-tbl-pager').style.display = totalPages > 1 ? 'flex' : 'none';

  const utilColor = u =>
    u==='ZERO-FILL' ? 'color:#198754;font-weight:600' :
    u==='LOW-FILL'  ? 'color:#fd7e14;font-weight:600' :
    u==='WORKING'   ? 'color:#0d6efd;font-weight:600' : '';
  const yesNo = v =>
    v==='Y' ? '<span class="badge badge-y">Yes</span>' :
    v==='N' ? '<span class="badge badge-n">No</span>' : '—';

  let html = '';
  slice.forEach(i => {
    const pwr = DV.POWER_SAVINGS[i];
    html += '<tr>'
      + '<td style="font-weight:600">' + (DV.BUILDING_CLLI[i]||'—') + '</td>'
      + '<td>' + (DV.DEVICE_NAME[i]||'—') + '</td>'
      + '<td>' + (DV.DEVICE_TYPE[i]||'—') + '</td>'
      + '<td data-val="' + (DV.CKT_COUNT[i]||0) + '">' + fmt(DV.CKT_COUNT[i]||0) + '</td>'
      + '<td style="' + utilColor(DV.DEVICE_UTILIZATION[i]) + '">' + (DV.DEVICE_UTILIZATION[i]||'—') + '</td>'
      + '<td>' + yesNo(DV.DEVICE_POWERED_DOWN[i]) + '</td>'
      + '<td>' + yesNo(DV.DEVICE_REMOVED[i]) + '</td>'
      + '<td style="font-size:.76rem">' + (DV.CURRENT_MILESTONE[i]||'—') + '</td>'
      + '<td>' + (DV.CURRENT_MILESTONE_STATUS[i]||'—') + '</td>'
      + '<td>' + (DV.MILESTONE_START[i]||'—') + '</td>'
      + '<td data-val="' + (pwr||0) + '">' + (pwr ? fmt(pwr) : '—') + '</td>'
      + '</tr>';
  });
  document.getElementById('dev-tbl-body').innerHTML = html ||
    '<tr><td colspan="11" style="text-align:center;color:#adb5bd;padding:20px">No records match filters</td></tr>';
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
initSummary();
initSwitches();
initCircuits();
initWaves();
initDevices();
initVelocity();
initKPIs();
</script>
</body>
</html>
"""

OUTPUT.write_text(html, encoding='utf-8')
print(f"\nOutput: {OUTPUT}")
print(f"File size: {OUTPUT.stat().st_size/1e6:.1f} MB")
print("Done!")
