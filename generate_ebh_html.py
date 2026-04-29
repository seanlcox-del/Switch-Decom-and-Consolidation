#!/usr/bin/env python3
"""
EBH (Ethernet Backhaul) Dashboard Generator
Usage:  python generate_ebh_html.py
Output: ebh_dashboard.html (Desktop)
"""
import json, re, warnings
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# ─── Config ───────────────────────────────────────────────────────────────────
DESKTOP     = Path(r"C:\Users\v296938\Desktop")
OUTPUT      = DESKTOP / "ebh_dashboard.html"
PLOTLY_FILE = DESKTOP / "SQDB OFS SITE FORECAST" / "plotly-2.35.2.min.js"
FWA_FILE    = DESKTOP / "04202026 FWA CBAND Capacity_Full Data.xlsx"

SNAP_META = [
    ("Jan 28", "2026-01-28", DESKTOP / "01282026 EBH Records Selected_Full Data_data (22).xlsx"),
    ("Mar 03", "2026-03-03", DESKTOP / "03032026 EBH Records Selected_Full Data_data (26).xlsx"),
    ("Apr 01", "2026-04-01", DESKTOP / "04012026 EBH Records Selected.xlsx"),
    ("Apr 16", "2026-04-16", DESKTOP / "04162026 EBH Records Selected_Full Data.xlsx"),
    ("Apr 23", "2026-04-23", DESKTOP / "04232026 EBH Records Selected_Full Data_data (36).xlsx"),
]

BW_ORDER       = ["<480M", "480M - 1G", "1G - 5G", "5G - 10G", ">= 10G"]
EBH_UTIL_ORDER = ["0-10", "10-20", "20-30", "30-40", "40-50", "50-60", "60-70", "70-80", "80-90", "90-100", ">= 100"]
RAN_UTIL_ORDER = ["0-10", "10-20", "20-30", "30-40", "40-50", "50-60", "60-70", "70-80", "80-90", "90-100"]

STATUS_MAP    = {"Open": 0, "Closed - Bandwidth<480": 1,
                 "Closed - 85% util for 4 weeks": 2, "Closed - No Hub capacity": 3}
STATUS_LABELS = ["Open", "Closed-BW<480", "Closed-Util85%", "Closed-NoHub"]

# ─── Helpers ──────────────────────────────────────────────────────────────────
def fix_pct(v):
    if isinstance(v, (pd.Timestamp, datetime)):
        return f"{v.month}-{v.day}"
    return "Unknown" if pd.isna(v) else str(v)

def sj(v):
    if isinstance(v, (np.integer,)):  return int(v)
    if isinstance(v, (np.floating,)): return None if np.isnan(v) else round(float(v), 4)
    if isinstance(v, float) and np.isnan(v): return None
    if isinstance(v, bool): return v
    return v

def parse_hub(v):
    if pd.isna(v): return None, None
    m = re.match(r"(\d+)\s*\((.*?)\)", str(v))
    if m: return m.group(1), "has ebh" in m.group(2).lower()
    return None, None

# ─── Load Plotly.js ───────────────────────────────────────────────────────────
if PLOTLY_FILE.exists():
    PLOTLY_JS = PLOTLY_FILE.read_text(encoding="utf-8")
    print(f"Plotly.js: {PLOTLY_FILE.stat().st_size/1024/1024:.1f} MB")
else:
    PLOTLY_JS = None
    print("Plotly.js not found — using CDN fallback")

# ─── Load FWA CBAND ───────────────────────────────────────────────────────────
print("Loading FWA CBAND...")
fwa_df = pd.read_excel(FWA_FILE, engine="openpyxl")[["Fuze Site ID", "Latitude", "Longitude", "On Air Date", "Site Name"]]
fwa_df["Fuze Site ID"] = fwa_df["Fuze Site ID"].astype(str)
site_geo = (fwa_df.groupby("Fuze Site ID")
            .agg(lat=("Latitude","first"), lng=("Longitude","first"),
                 on_air=("On Air Date","first"), site_name=("Site Name","first"))
            .to_dict("index"))
print(f"  {len(site_geo):,} sites with geo/on-air data")

# ─── Load EBH snapshots ───────────────────────────────────────────────────────
print("Loading EBH snapshots...")
snap_dfs = {}
for label, date_str, fpath in SNAP_META:
    if not fpath.exists():
        print(f"  ! Missing: {fpath.name}"); continue
    df = pd.read_excel(fpath)
    for col in ["EBH Usage % Group", "RAN Used Percent group"]:
        if col in df.columns:
            df[col] = df[col].apply(fix_pct)
    df["Fuze Site ID"] = df["Fuze Site ID"].astype(str)
    snap_dfs[date_str] = (label, df)
    print(f"  + {label} ({date_str}): {len(df):,} rows")

snap_keys   = sorted(snap_dfs.keys())
snap_labels = [snap_dfs[k][0] for k in snap_keys]
latest_key  = snap_keys[-1]
_, latest_df = snap_dfs[latest_key]

# ─── Hub-spoke ────────────────────────────────────────────────────────────────
print("Building hub-spoke relationships...")
ldf = latest_df.copy()
ldf[["hub_fuze_id","hub_has_ebh"]] = ldf["hub_site"].apply(lambda v: pd.Series(parse_hub(v)))

hub_to_spokes = {}
spoke_to_hub  = {}
for _, row in ldf[ldf["hub_fuze_id"].notna()].iterrows():
    h = str(row["hub_fuze_id"])
    s = str(row["Fuze Site ID"])
    hub_to_spokes.setdefault(h, []).append(s)
    spoke_to_hub[s] = h
print(f"  {len(hub_to_spokes):,} hubs, {len(spoke_to_hub):,} spokes")

# ─── Summary ──────────────────────────────────────────────────────────────────
print("Summary data...")
summary_data = {}
for k in snap_keys:
    lbl, df = snap_dfs[k]
    open_df   = df[df["Open/Closed Status"] == "Open"]
    closed_df = df[df["Open/Closed Status"] != "Open"]
    def _s(col, ddf=df): return int(ddf[col].fillna(0).sum()) if col in ddf.columns else 0
    def _m(col, ddf=df): return round(float(ddf[col].fillna(0).mean()), 4) if col in ddf.columns else None
    summary_data[k] = {
        "label": lbl, "total": len(df),
        "open": len(open_df), "closed": len(closed_df),
        "valid_ebh": int((df["Valid EBH"] == True).sum()),
        "invalid_ebh": int((df["Valid EBH"] != True).sum()),
        "ofs_count": int(open_df["OFS Count"].fillna(0).sum()),
        "avail_hh": _s("EBH Available Households**"),
        "eff_fwa_gbps": round(_s("Effective Available FWA Bandwidth(mbps)*") / 1000, 1),
        "avg_7d_util": _m("7 Day EBH Util %"),
        "avg_30d_util": _m("30 Day EBH Util%"),
        "avg_ran_pct": _m("RAN Used Percent"),
        "closed_reasons": {str(r): int(c) for r, c in closed_df["Open/Closed Status"].value_counts().items()},
    }

# ─── Bandwidth ────────────────────────────────────────────────────────────────
print("Bandwidth data...")
bw_data = {}
for k in snap_keys:
    lbl, df = snap_dfs[k]
    vc     = df["Bandwidth Group"].value_counts().to_dict()
    vc_ofs = df.groupby("Bandwidth Group")["OFS Count"].sum().to_dict()
    bw_data[k] = {
        "label": lbl,
        "counts": [int(vc.get(g, 0)) for g in BW_ORDER],
        "ofs":    [int(vc_ofs.get(g, 0)) for g in BW_ORDER],
        "open":   [int(df[(df["Bandwidth Group"]==g)&(df["Open/Closed Status"]=="Open")].shape[0]) for g in BW_ORDER],
    }

# ─── Utilization ──────────────────────────────────────────────────────────────
print("Utilization data...")
util_data = {}
for k in snap_keys:
    lbl, df = snap_dfs[k]
    eu = df["EBH Usage % Group"].value_counts().to_dict()
    ru = df["RAN Used Percent group"].value_counts().to_dict()
    util_data[k] = {
        "label": lbl,
        "ebh_counts": [int(eu.get(g, 0)) for g in EBH_UTIL_ORDER],
        "ran_counts":  [int(ru.get(g, 0)) for g in RAN_UTIL_ORDER],
    }

high_util_rows = []
hi_groups = ["50-60","60-70","70-80","80-90","90-100",">= 100"]
hi_cols   = ["Fuze Site ID","Site Name","CMA","Sub Market","EBH Usage % Group",
             "RAN Used Percent","Bandwidth Group","OFS Count","Open/Closed Status","7 Day EBH Util %"]
avail_hi  = [c for c in hi_cols if c in latest_df.columns]
hi_df     = latest_df[latest_df["EBH Usage % Group"].isin(hi_groups)][avail_hi]
for _, row in hi_df.iterrows():
    high_util_rows.append({c: sj(row[c]) for c in avail_hi})
print(f"  High-util sites: {len(high_util_rows)}")

# ─── Sub Market + CMA ─────────────────────────────────────────────────────────
print("Sub Market / CMA data...")
submarket_data, cma_data = {}, {}
for k in snap_keys:
    lbl, df = snap_dfs[k]
    sm_rows, sm_cma = [], {}
    for sm, gdf in df.groupby("Sub Market", dropna=True):
        sm = str(sm)
        sm_rows.append({
            "name": sm, "total": len(gdf),
            "open": int((gdf["Open/Closed Status"]=="Open").sum()),
            "valid_ebh": int((gdf["Valid EBH"]==True).sum()),
            "ofs_count": int(gdf["OFS Count"].fillna(0).sum()),
            "avail_hh": int(gdf["EBH Available Households**"].fillna(0).sum()) if "EBH Available Households**" in gdf.columns else 0,
            "avg_7d": round(float(gdf["7 Day EBH Util %"].fillna(0).mean()), 4) if "7 Day EBH Util %" in gdf.columns else 0,
            "avg_30d": round(float(gdf["30 Day EBH Util%"].fillna(0).mean()), 4) if "30 Day EBH Util%" in gdf.columns else None,
            "avg_ran": round(float(gdf["RAN Used Percent"].fillna(0).mean()), 1) if "RAN Used Percent" in gdf.columns else 0,
            "bw": {g: int((gdf["Bandwidth Group"]==g).sum()) for g in BW_ORDER},
        })
        if "CMA" in df.columns:
            cma_rows = []
            for cma, cgdf in gdf.groupby("CMA", dropna=True):
                cma_rows.append({
                    "name": str(cma), "total": len(cgdf),
                    "open": int((cgdf["Open/Closed Status"]=="Open").sum()),
                    "ofs_count": int(cgdf["OFS Count"].fillna(0).sum()),
                    "avg_ran": round(float(cgdf["RAN Used Percent"].fillna(0).mean()), 1) if "RAN Used Percent" in cgdf.columns else 0,
                    "avail_hh": int(cgdf["EBH Available Households**"].fillna(0).sum()) if "EBH Available Households**" in cgdf.columns else 0,
                })
            sm_cma[sm] = sorted(cma_rows, key=lambda r: r["ofs_count"], reverse=True)
    sm_rows.sort(key=lambda r: r["ofs_count"], reverse=True)
    submarket_data[k] = sm_rows
    cma_data[k] = sm_cma

# ─── Transport Type ───────────────────────────────────────────────────────────
print("Transport Type data...")
transport_data = {}
for k in snap_keys:
    lbl, df = snap_dfs[k]
    rows = []
    for tt, gdf in df.groupby("Transport Type", dropna=True):
        rows.append({
            "type": str(tt), "count": len(gdf),
            "pct": round(len(gdf)/len(df)*100, 1),
            "open": int((gdf["Open/Closed Status"]=="Open").sum()),
            "valid_ebh": int((gdf["Valid EBH"]==True).sum()),
            "ofs_count": int(gdf["OFS Count"].fillna(0).sum()),
            "avg_bw_gbps": round(float(gdf["Bandwidth (mbps)"].fillna(0).mean())/1000, 2) if "Bandwidth (mbps)" in gdf.columns else 0,
            "avail_hh": int(gdf["EBH Available Households**"].fillna(0).sum()) if "EBH Available Households**" in gdf.columns else 0,
        })
    rows.sort(key=lambda r: r["count"], reverse=True)
    transport_data[k] = rows

# ─── Closed Sites ─────────────────────────────────────────────────────────────
print("Closed sites data...")
closed_cols = ["Fuze Site ID","Site Name","EBH ID","CMA","Sub Market",
               "Open/Closed Status","Bandwidth Group","Bandwidth (mbps)",
               "Transport Type","OFS Count","Valid EBH","Missing reason","Note"]
closed_data = {}
for k in snap_keys:
    lbl, df = snap_dfs[k]
    cdf   = df[df["Open/Closed Status"] != "Open"].copy()
    avail = [c for c in closed_cols if c in cdf.columns]
    closed_data[k] = [{c: sj(row[c]) for c in avail} for _, row in cdf[avail].iterrows()]

# ─── Hub-Spoke detail ─────────────────────────────────────────────────────────
print("Hub-spoke detail...")
hub_detail = {}
for hub_id in hub_to_spokes:
    rows = ldf[ldf["Fuze Site ID"] == hub_id]
    if len(rows):
        r = rows.iloc[0]
        hub_detail[hub_id] = {
            "bw":      str(r.get("Bandwidth Group","?")),
            "status":  str(r.get("Open/Closed Status","?")),
            "sm":      str(r.get("Sub Market","?")),
            "cma":     str(r.get("CMA","?")),
            "ofs":     int(r.get("OFS Count",0)) if pd.notna(r.get("OFS Count")) else 0,
            "transport": str(r.get("Transport Type","?")),
            "valid_ebh": bool(r.get("Valid EBH", False)),
            "spokes":  len(hub_to_spokes[hub_id]),
        }
    else:
        geo = site_geo.get(hub_id, {})
        hub_detail[hub_id] = {
            "bw":"?","status":"Not in dataset","sm":"?","cma":str(geo.get("site_name","?")),
            "ofs":0,"transport":"?","valid_ebh":False,"spokes":len(hub_to_spokes[hub_id])
        }

# ─── Map data (column arrays) ─────────────────────────────────────────────────
print("Map data...")
sub_markets_all = sorted(latest_df["Sub Market"].dropna().unique().tolist())
cma_list_all    = sorted(latest_df["CMA"].dropna().unique().tolist())
sm_idx_map  = {sm: i for i, sm in enumerate(sub_markets_all)}
cma_idx_map = {c: i for i, c in enumerate(cma_list_all)}

m_ids, m_lats, m_lngs, m_bw, m_st, m_ofs, m_eu, m_sm, m_cma, m_on_air = \
    [], [], [], [], [], [], [], [], [], []

for _, row in latest_df.iterrows():
    fid  = str(row["Fuze Site ID"])
    geo  = site_geo.get(fid)
    if not geo or pd.isna(geo.get("lat")): continue
    bw   = str(row.get("Bandwidth Group",""))
    st   = str(row.get("Open/Closed Status","Open"))
    eu   = str(row.get("EBH Usage % Group","0-10"))
    sm   = str(row.get("Sub Market","")) if pd.notna(row.get("Sub Market")) else ""
    cma  = str(row.get("CMA",""))        if pd.notna(row.get("CMA"))        else ""
    oa   = str(geo["on_air"]) if geo.get("on_air") and str(geo["on_air"]) not in ("nan","None") else None
    m_ids.append(int(row["Fuze Site ID"]))
    m_lats.append(round(float(geo["lat"]), 4))
    m_lngs.append(round(float(geo["lng"]), 4))
    m_bw.append(BW_ORDER.index(bw) if bw in BW_ORDER else -1)
    m_st.append(STATUS_MAP.get(st, 0))
    m_ofs.append(int(row.get("OFS Count",0)) if pd.notna(row.get("OFS Count")) else 0)
    m_eu.append(EBH_UTIL_ORDER.index(eu) if eu in EBH_UTIL_ORDER else 0)
    m_sm.append(sm_idx_map.get(sm, -1))
    m_cma.append(cma_idx_map.get(cma, -1))
    m_on_air.append(oa)

map_data = {
    "ids": m_ids, "lats": m_lats, "lngs": m_lngs,
    "bw": m_bw, "st": m_st, "ofs": m_ofs,
    "eu": m_eu, "sm": m_sm, "cma": m_cma, "on_air": m_on_air,
    "sub_markets": sub_markets_all, "cma_list": cma_list_all,
}
print(f"  Map sites: {len(m_ids):,}")

# ─── Assemble payload ─────────────────────────────────────────────────────────
print("Serializing...")
dashboard_data = {
    "snapshots": snap_keys, "snap_labels": snap_labels, "latest": latest_key,
    "bw_groups": BW_ORDER, "ebh_util_groups": EBH_UTIL_ORDER,
    "ran_util_groups": RAN_UTIL_ORDER, "status_labels": STATUS_LABELS,
    "summary": summary_data, "bandwidth": bw_data, "utilization": util_data,
    "high_util": high_util_rows, "submarket": submarket_data, "cma": cma_data,
    "transport": transport_data, "closed": closed_data,
    "hub_detail": hub_detail, "hub_to_spokes": hub_to_spokes,
    "spoke_to_hub": spoke_to_hub, "map_data": map_data,
}
DATA_JSON = json.dumps(dashboard_data, ensure_ascii=False, default=str)
print(f"Data size: {len(DATA_JSON)/1024/1024:.2f} MB")

# ═══════════════════════════════════════════════════════════════════════════════
#  HTML TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EBH Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f1923;color:#e8edf2;font-family:'Segoe UI',system-ui,sans-serif;font-size:14px}
a{color:#60a5fa}

/* Header */
.hdr{background:linear-gradient(135deg,#0d1f2d 0%,#1a2f44 100%);padding:18px 28px;
  border-bottom:1px solid #2a3b50;display:flex;align-items:center;gap:16px}
.hdr h1{font-size:20px;font-weight:700;color:#fff}
.hdr .sub{color:#7a8ba0;font-size:13px;margin-left:auto}

/* Tabs */
.tab-nav{display:flex;gap:4px;padding:12px 20px 0;background:#0d1520;
  border-bottom:1px solid #2a3b50;flex-wrap:wrap}
.tab-btn{background:none;border:none;color:#7a8ba0;padding:8px 16px;cursor:pointer;
  font-size:13px;font-weight:500;border-radius:6px 6px 0 0;
  border:1px solid transparent;border-bottom:none;transition:all .2s}
.tab-btn:hover{color:#e8edf2;background:#1a2535}
.tab-btn.active{color:#fff;background:#1a2535;border-color:#2a3b50;border-bottom-color:#1a2535;
  position:relative;top:1px}

/* Content */
.tab-panel{display:none;padding:20px}
.tab-panel.active{display:block}

/* KPI Cards */
.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:#1a2535;border:1px solid #2a3b50;border-radius:8px;padding:14px 16px;
  border-top:3px solid #2a3b50}
.kpi.c-blue{border-top-color:#0d6efd}
.kpi.c-green{border-top-color:#198754}
.kpi.c-orange{border-top-color:#fd7e14}
.kpi.c-purple{border-top-color:#6f42c1}
.kpi.c-red{border-top-color:#dc3545}
.kpi.c-teal{border-top-color:#20c997}
.kpi-label{font-size:11px;color:#7a8ba0;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.kpi-value{font-size:22px;font-weight:700;color:#fff}
.kpi-sub{font-size:11px;color:#7a8ba0;margin-top:2px}

/* Snap selector */
.snap-sel{display:flex;gap:6px;margin-bottom:16px;flex-wrap:wrap;align-items:center}
.snap-sel label{font-size:12px;color:#7a8ba0;margin-right:4px}
.snap-btn{background:#1a2535;border:1px solid #2a3b50;color:#7a8ba0;padding:5px 12px;
  border-radius:4px;cursor:pointer;font-size:12px;transition:all .15s}
.snap-btn:hover{border-color:#0d6efd;color:#e8edf2}
.snap-btn.active{background:#0d6efd;border-color:#0d6efd;color:#fff}

/* Cards / panels */
.card{background:#1a2535;border:1px solid #2a3b50;border-radius:8px;padding:16px;margin-bottom:16px}
.card-title{font-size:13px;font-weight:600;color:#9ab;margin-bottom:12px;
  text-transform:uppercase;letter-spacing:.5px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
@media(max-width:900px){.row2,.row3{grid-template-columns:1fr}}

/* Tables */
.tbl-wrap{overflow-x:auto;max-height:420px;overflow-y:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{background:#0d1520;color:#7a8ba0;text-transform:uppercase;font-size:11px;
  letter-spacing:.4px;padding:8px 10px;position:sticky;top:0;z-index:1;
  border-bottom:1px solid #2a3b50;cursor:pointer;user-select:none;white-space:nowrap}
thead th:hover{color:#e8edf2}
tbody tr{border-bottom:1px solid #1e2d40}
tbody tr:hover{background:#1e2d40}
tbody td{padding:7px 10px;color:#c0cdd8}
.badge{display:inline-block;padding:2px 7px;border-radius:3px;font-size:10px;font-weight:600}
.b-green{background:#0d3320;color:#4ade80}
.b-red{background:#3b0a0a;color:#f87171}
.b-orange{background:#3a1a00;color:#fb923c}
.b-purple{background:#2a1040;color:#c084fc}
.b-blue{background:#0a1f40;color:#60a5fa}
.b-gray{background:#1e2d40;color:#7a8ba0}

/* Search / filter */
.filter-row{display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
.filter-row input,.filter-row select{
  background:#0d1520;border:1px solid #2a3b50;color:#e8edf2;
  padding:6px 10px;border-radius:4px;font-size:12px;min-width:140px}
.filter-row input::placeholder{color:#4a5b6e}
.filter-row label{font-size:12px;color:#7a8ba0}

/* Chart containers */
.chart{width:100%;height:340px}
.chart-lg{width:100%;height:420px}
.chart-sm{width:100%;height:280px}
.chart-map{width:100%;height:560px}

/* Divider */
.divider{height:1px;background:#2a3b50;margin:16px 0}

/* Hub-spoke card */
.hub-card{background:#0d1520;border:1px solid #2a3b50;border-radius:6px;padding:12px;margin-top:12px}
.hub-card h4{font-size:13px;font-weight:600;color:#60a5fa;margin-bottom:8px}
.hub-meta{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.hub-meta span{font-size:12px;color:#7a8ba0}
.hub-meta span b{color:#e8edf2}

/* Map controls */
.map-ctrl{display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
.map-ctrl select{background:#0d1520;border:1px solid #2a3b50;color:#e8edf2;
  padding:5px 10px;border-radius:4px;font-size:12px}
.map-legend{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;align-items:center}
.leg-item{display:flex;align-items:center;gap:5px;font-size:11px;color:#9ab}
.leg-dot{width:10px;height:10px;border-radius:50%}

/* Tooltip */
#tooltip{position:fixed;background:#1a2535;border:1px solid #2a3b50;border-radius:6px;
  padding:8px 12px;font-size:11px;color:#e8edf2;pointer-events:none;
  display:none;z-index:9999;max-width:260px;line-height:1.6}
</style>
</head>
<body>
<div id="tooltip"></div>

<div class="hdr">
  <div>
    <h1>&#x1F4E1; EBH (Ethernet Backhaul) Dashboard</h1>
  </div>
  <div class="sub">Latest snapshot: Apr 23, 2026 &nbsp;|&nbsp; 5 snapshots loaded</div>
</div>

<nav class="tab-nav">
  <button class="tab-btn active" onclick="showTab('summary')">Summary</button>
  <button class="tab-btn" onclick="showTab('bandwidth')">Bandwidth Groups</button>
  <button class="tab-btn" onclick="showTab('utilization')">Utilization</button>
  <button class="tab-btn" onclick="showTab('submarket')">Sub Market</button>
  <button class="tab-btn" onclick="showTab('transport')">Transport Type</button>
  <button class="tab-btn" onclick="showTab('closed')">Closed Sites</button>
  <button class="tab-btn" onclick="showTab('hubspoke')">Hub-Spoke</button>
  <button class="tab-btn" onclick="showTab('mapview')">Map</button>
</nav>

<!-- ═══ SUMMARY ═══════════════════════════════════════════════════════════════ -->
<div id="tab-summary" class="tab-panel active">
  <div class="snap-sel">
    <label>Snapshot:</label>
    <span id="sum-snap-btns"></span>
  </div>
  <div id="sum-kpi" class="kpi-row"></div>
  <div class="row2">
    <div class="card"><div class="card-title">Total Sites — Week over Week</div><div id="chrt-wow-sites" class="chart"></div></div>
    <div class="card"><div class="card-title">Valid EBH &amp; OFS Count — WoW</div><div id="chrt-wow-ofs" class="chart"></div></div>
  </div>
  <div class="row2">
    <div class="card"><div class="card-title">Closed Site Reasons (Selected Snapshot)</div><div id="chrt-closed-pie" class="chart-sm"></div></div>
    <div class="card"><div class="card-title">Available Households — WoW</div><div id="chrt-wow-hh" class="chart-sm"></div></div>
  </div>
</div>

<!-- ═══ BANDWIDTH ═════════════════════════════════════════════════════════════ -->
<div id="tab-bandwidth" class="tab-panel">
  <div class="snap-sel"><label>Snapshot:</label><span id="bw-snap-btns"></span></div>
  <div class="row2">
    <div class="card"><div class="card-title">Site Count by Bandwidth Group</div><div id="chrt-bw-count" class="chart"></div></div>
    <div class="card"><div class="card-title">OFS Count by Bandwidth Group</div><div id="chrt-bw-ofs" class="chart"></div></div>
  </div>
  <div class="card"><div class="card-title">Bandwidth Groups — All Snapshots</div><div id="chrt-bw-trend" class="chart-lg"></div></div>
</div>

<!-- ═══ UTILIZATION ═══════════════════════════════════════════════════════════ -->
<div id="tab-utilization" class="tab-panel">
  <div class="snap-sel"><label>Snapshot:</label><span id="util-snap-btns"></span></div>
  <div class="row2">
    <div class="card"><div class="card-title">EBH Utilization % Distribution</div><div id="chrt-ebh-util" class="chart"></div></div>
    <div class="card"><div class="card-title">RAN Used % Distribution</div><div id="chrt-ran-util" class="chart"></div></div>
  </div>
  <div class="card">
    <div class="card-title">High Utilization Sites — EBH &ge; 50% (Latest Snapshot)</div>
    <div class="filter-row">
      <input id="hi-search" placeholder="Search site / CMA / Sub Market..." oninput="filterHiUtil()">
      <select id="hi-bw-filter" onchange="filterHiUtil()">
        <option value="">All BW Groups</option>
      </select>
    </div>
    <div class="tbl-wrap"><table id="hi-tbl">
      <thead><tr>
        <th onclick="sortTbl('hi-tbl',0)">Fuze Site ID</th>
        <th onclick="sortTbl('hi-tbl',1)">Site Name</th>
        <th onclick="sortTbl('hi-tbl',2)">CMA</th>
        <th onclick="sortTbl('hi-tbl',3)">Sub Market</th>
        <th onclick="sortTbl('hi-tbl',4)">EBH Util %</th>
        <th onclick="sortTbl('hi-tbl',5)">RAN Used %</th>
        <th onclick="sortTbl('hi-tbl',6)">BW Group</th>
        <th onclick="sortTbl('hi-tbl',7)">OFS Count</th>
        <th onclick="sortTbl('hi-tbl',8)">Status</th>
      </tr></thead>
      <tbody id="hi-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- ═══ SUB MARKET ════════════════════════════════════════════════════════════ -->
<div id="tab-submarket" class="tab-panel">
  <div class="snap-sel"><label>Snapshot:</label><span id="sm-snap-btns"></span></div>
  <div class="card"><div class="card-title">OFS Count by Sub Market</div><div id="chrt-sm-bar" class="chart-lg"></div></div>
  <div class="card">
    <div class="card-title">Sub Market Metrics</div>
    <div class="filter-row">
      <input id="sm-search" placeholder="Filter sub market..." oninput="filterSmTbl()">
    </div>
    <div class="tbl-wrap"><table id="sm-tbl">
      <thead><tr>
        <th onclick="sortTbl('sm-tbl',0)">Sub Market</th>
        <th onclick="sortTbl('sm-tbl',1)">Total Sites</th>
        <th onclick="sortTbl('sm-tbl',2)">Open</th>
        <th onclick="sortTbl('sm-tbl',3)">Closed</th>
        <th onclick="sortTbl('sm-tbl',4)">Valid EBH</th>
        <th onclick="sortTbl('sm-tbl',5)">OFS Count</th>
        <th onclick="sortTbl('sm-tbl',6)">Avail HH</th>
        <th onclick="sortTbl('sm-tbl',7)">Avg 7d EBH%</th>
        <th onclick="sortTbl('sm-tbl',8)">Avg RAN%</th>
      </tr></thead>
      <tbody id="sm-tbl-body"></tbody>
    </table></div>
  </div>
  <div class="card" id="cma-section" style="display:none">
    <div class="card-title" id="cma-title">CMA Drill-Down</div>
    <div class="tbl-wrap"><table id="cma-tbl">
      <thead><tr>
        <th onclick="sortTbl('cma-tbl',0)">CMA</th>
        <th onclick="sortTbl('cma-tbl',1)">Total Sites</th>
        <th onclick="sortTbl('cma-tbl',2)">Open</th>
        <th onclick="sortTbl('cma-tbl',3)">OFS Count</th>
        <th onclick="sortTbl('cma-tbl',4)">Avail HH</th>
        <th onclick="sortTbl('cma-tbl',5)">Avg RAN%</th>
      </tr></thead>
      <tbody id="cma-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- ═══ TRANSPORT ═════════════════════════════════════════════════════════════ -->
<div id="tab-transport" class="tab-panel">
  <div class="snap-sel"><label>Snapshot:</label><span id="tt-snap-btns"></span></div>
  <div class="row2">
    <div class="card"><div class="card-title">Distribution by Transport Type</div><div id="chrt-tt-pie" class="chart"></div></div>
    <div class="card"><div class="card-title">OFS Count by Transport Type</div><div id="chrt-tt-ofs" class="chart"></div></div>
  </div>
  <div class="card">
    <div class="card-title">Transport Type Metrics</div>
    <div class="tbl-wrap"><table id="tt-tbl">
      <thead><tr>
        <th onclick="sortTbl('tt-tbl',0)">Transport Type</th>
        <th onclick="sortTbl('tt-tbl',1)">Sites</th>
        <th onclick="sortTbl('tt-tbl',2)">%</th>
        <th onclick="sortTbl('tt-tbl',3)">Open</th>
        <th onclick="sortTbl('tt-tbl',4)">Valid EBH</th>
        <th onclick="sortTbl('tt-tbl',5)">OFS Count</th>
        <th onclick="sortTbl('tt-tbl',6)">Avail HH</th>
        <th onclick="sortTbl('tt-tbl',7)">Avg BW (Gbps)</th>
      </tr></thead>
      <tbody id="tt-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- ═══ CLOSED SITES ══════════════════════════════════════════════════════════ -->
<div id="tab-closed" class="tab-panel">
  <div class="snap-sel"><label>Snapshot:</label><span id="cl-snap-btns"></span></div>
  <div id="cl-kpi" class="kpi-row"></div>
  <div class="card">
    <div class="card-title">Closed Sites Detail</div>
    <div class="filter-row">
      <input id="cl-search" placeholder="Search site / CMA..." oninput="filterClosed()">
      <select id="cl-reason-filter" onchange="filterClosed()">
        <option value="">All Reasons</option>
        <option value="Bandwidth">Bandwidth &lt; 480</option>
        <option value="85%">85% Utilization</option>
        <option value="No Hub">No Hub Capacity</option>
      </select>
      <select id="cl-bw-filter" onchange="filterClosed()">
        <option value="">All BW Groups</option>
      </select>
    </div>
    <div class="tbl-wrap"><table id="cl-tbl">
      <thead><tr>
        <th onclick="sortTbl('cl-tbl',0)">Fuze Site ID</th>
        <th onclick="sortTbl('cl-tbl',1)">Site Name</th>
        <th onclick="sortTbl('cl-tbl',2)">CMA</th>
        <th onclick="sortTbl('cl-tbl',3)">Sub Market</th>
        <th onclick="sortTbl('cl-tbl',4)">Closure Reason</th>
        <th onclick="sortTbl('cl-tbl',5)">BW Group</th>
        <th onclick="sortTbl('cl-tbl',6)">BW (Mbps)</th>
        <th onclick="sortTbl('cl-tbl',7)">Transport</th>
        <th onclick="sortTbl('cl-tbl',8)">OFS</th>
        <th onclick="sortTbl('cl-tbl',9)">Valid EBH</th>
      </tr></thead>
      <tbody id="cl-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- ═══ HUB-SPOKE ═════════════════════════════════════════════════════════════ -->
<div id="tab-hubspoke" class="tab-panel">
  <div class="kpi-row">
    <div class="kpi c-blue"><div class="kpi-label">Hub Sites</div><div class="kpi-value" id="hs-kpi-hubs">—</div><div class="kpi-sub">with spoke relationships</div></div>
    <div class="kpi c-green"><div class="kpi-label">Spoke Sites</div><div class="kpi-value" id="hs-kpi-spokes">—</div><div class="kpi-sub">connected to a hub</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Avg Spokes/Hub</div><div class="kpi-value" id="hs-kpi-avg">—</div><div class="kpi-sub">in latest snapshot</div></div>
  </div>

  <div class="card">
    <div class="card-title">Site Lookup by Fuze Site ID</div>
    <div class="filter-row">
      <input id="hs-search" placeholder="Enter Fuze Site ID..." oninput="hsLookup(this.value)" style="min-width:220px">
    </div>
    <div id="hs-result"></div>
  </div>

  <div class="card">
    <div class="card-title">Hub Sites — Top by Spoke Count</div>
    <div class="filter-row">
      <input id="hs-hub-search" placeholder="Filter hub ID / Sub Market..." oninput="filterHubTbl()">
      <select id="hs-sm-filter" onchange="filterHubTbl()"><option value="">All Sub Markets</option></select>
    </div>
    <div class="tbl-wrap"><table id="hub-tbl">
      <thead><tr>
        <th onclick="sortTbl('hub-tbl',0)">Hub Fuze Site ID</th>
        <th onclick="sortTbl('hub-tbl',1)">Sub Market</th>
        <th onclick="sortTbl('hub-tbl',2)">CMA</th>
        <th onclick="sortTbl('hub-tbl',3)">BW Group</th>
        <th onclick="sortTbl('hub-tbl',4)">Status</th>
        <th onclick="sortTbl('hub-tbl',5)">Spoke Count</th>
        <th onclick="sortTbl('hub-tbl',6)">Transport</th>
        <th onclick="sortTbl('hub-tbl',7)">Valid EBH</th>
      </tr></thead>
      <tbody id="hub-tbl-body"></tbody>
    </table></div>
  </div>
</div>

<!-- ═══ MAP ═══════════════════════════════════════════════════════════════════ -->
<div id="tab-mapview" class="tab-panel">
  <div class="map-ctrl">
    <label style="font-size:12px;color:#7a8ba0">Color by:</label>
    <select id="map-color-mode" onchange="renderMap()">
      <option value="bw">Bandwidth Group</option>
      <option value="status">Open/Closed Status</option>
      <option value="util">EBH Util %</option>
    </select>
    <label style="font-size:12px;color:#7a8ba0">Sub Market:</label>
    <select id="map-sm-filter" onchange="renderMap()">
      <option value="-1">All Sub Markets</option>
    </select>
  </div>
  <div id="map-legend" class="map-legend"></div>
  <div class="card" style="padding:8px">
    <div id="chrt-map" class="chart-map"></div>
  </div>
  <div id="map-info" class="card" style="display:none;margin-top:10px">
    <div class="card-title">Selected Site</div>
    <div id="map-info-body"></div>
  </div>
</div>

<!-- ═══ DATA + SCRIPTS ════════════════════════════════════════════════════════ -->
<script>
const DATA = DATA_PLACEHOLDER;
</script>
PLOTLY_PLACEHOLDER
<script>
// ── Utilities ─────────────────────────────────────────────────────────────────
const fmt = n => n == null ? '—' : Number(n).toLocaleString();
const pct = v => v == null ? '—' : (v*100).toFixed(2) + '%';
const snap = k => DATA.snapshots.indexOf(k);

const BW_COLORS  = ['#dc3545','#fd7e14','#ffc107','#198754','#0d6efd'];
const ST_COLORS  = ['#198754','#dc3545','#fd7e14','#6f42c1'];
const EU_COLORS  = ['#198754','#5a9','#b8c','#ffc107','#fd7e14','#e85','#dc3545','#c20','#900','#600','#300'];
const DARK_LAYOUT = {
  paper_bgcolor:'#1a2535', plot_bgcolor:'#1a2535',
  font:{color:'#9ab',size:11},
  margin:{l:50,r:20,t:30,b:50},
  legend:{bgcolor:'#0d1520',bordercolor:'#2a3b50',borderwidth:1},
  xaxis:{gridcolor:'#2a3b50',zerolinecolor:'#2a3b50'},
  yaxis:{gridcolor:'#2a3b50',zerolinecolor:'#2a3b50'},
};
function mkLayout(extra){return Object.assign({},DARK_LAYOUT,extra)}

// ── Tab switching ─────────────────────────────────────────────────────────────
const rendered = {};
function showTab(name){
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  event.currentTarget.classList.add('active');
  if(!rendered[name]){ rendered[name]=true; initTab(name); }
  if(name==='mapview' && !rendered._mapInit){ rendered._mapInit=true; renderMap(); }
}

// ── Snapshot selector builder ─────────────────────────────────────────────────
function snapBtns(containerId, onClickFn, defaultIdx){
  const el = document.getElementById(containerId);
  DATA.snap_labels.forEach((lbl,i)=>{
    const b = document.createElement('button');
    b.className = 'snap-btn' + (i===defaultIdx?' active':'');
    b.textContent = lbl;
    b.dataset.key = DATA.snapshots[i];
    b.onclick = function(){
      el.querySelectorAll('.snap-btn').forEach(x=>x.classList.remove('active'));
      this.classList.add('active');
      onClickFn(this.dataset.key);
    };
    el.appendChild(b);
  });
}
function activeSnap(containerId){ return document.querySelector('#'+containerId+' .snap-btn.active').dataset.key; }

// ── Sort tables ───────────────────────────────────────────────────────────────
const sortState = {};
function sortTbl(id, col){
  const tbl = document.getElementById(id);
  const tbody = tbl.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const asc = (sortState[id]===col) ? !sortState[id+'_asc'] : true;
  sortState[id]=col; sortState[id+'_asc']=asc;
  rows.sort((a,b)=>{
    const av = a.cells[col]?.dataset?.val ?? a.cells[col]?.textContent ?? '';
    const bv = b.cells[col]?.dataset?.val ?? b.cells[col]?.textContent ?? '';
    const an = parseFloat(av), bn = parseFloat(bv);
    if(!isNaN(an)&&!isNaN(bn)) return asc?an-bn:bn-an;
    return asc?av.localeCompare(bv):bv.localeCompare(av);
  });
  rows.forEach(r=>tbody.appendChild(r));
}

// ── Status badge ──────────────────────────────────────────────────────────────
function stBadge(st){
  if(st==='Open') return '<span class="badge b-green">Open</span>';
  if(st&&st.includes('480')) return '<span class="badge b-red">BW&lt;480</span>';
  if(st&&st.includes('85%')) return '<span class="badge b-orange">Util 85%</span>';
  if(st&&st.includes('Hub')) return '<span class="badge b-purple">No Hub</span>';
  return `<span class="badge b-gray">${st||'?'}</span>`;
}
function bwBadge(bw){
  const idx = DATA.bw_groups.indexOf(bw);
  const cls = ['b-red','b-orange','b-gray','b-green','b-blue'][idx] || 'b-gray';
  return `<span class="badge ${cls}">${bw||'?'}</span>`;
}
function td(v,align){ return `<td style="${align?'text-align:'+align:''}" data-val="${v??''}">${v??'—'}</td>`; }

// ═══ SUMMARY ═════════════════════════════════════════════════════════════════
function initTab(name){
  if(name==='summary') initSummary();
  else if(name==='bandwidth') initBandwidth();
  else if(name==='utilization') initUtilization();
  else if(name==='submarket') initSubmarket();
  else if(name==='transport') initTransport();
  else if(name==='closed') initClosed();
  else if(name==='hubspoke') initHubSpoke();
}

let _sumSnap = DATA.latest;
function initSummary(){
  snapBtns('sum-snap-btns', k=>{ _sumSnap=k; renderSumKpi(); renderClosedPie(); }, DATA.snapshots.length-1);
  renderSumKpi();
  renderWoW();
  renderClosedPie();
}
function renderSumKpi(){
  const s = DATA.summary[_sumSnap];
  document.getElementById('sum-kpi').innerHTML = `
    <div class="kpi c-blue"><div class="kpi-label">Total Sites</div><div class="kpi-value">${fmt(s.total)}</div><div class="kpi-sub">${s.label}</div></div>
    <div class="kpi c-green"><div class="kpi-label">Open Sites</div><div class="kpi-value">${fmt(s.open)}</div><div class="kpi-sub">${((s.open/s.total)*100).toFixed(1)}% of total</div></div>
    <div class="kpi c-red"><div class="kpi-label">Closed Sites</div><div class="kpi-value">${fmt(s.closed)}</div><div class="kpi-sub">${Object.keys(s.closed_reasons).length} reason(s)</div></div>
    <div class="kpi c-teal"><div class="kpi-label">Valid EBH</div><div class="kpi-value">${fmt(s.valid_ebh)}</div><div class="kpi-sub">${fmt(s.invalid_ebh)} invalid</div></div>
    <div class="kpi c-orange"><div class="kpi-label">Total OFS</div><div class="kpi-value">${(s.ofs_count/1e6).toFixed(2)}M</div><div class="kpi-sub">Open sites only</div></div>
    <div class="kpi c-purple"><div class="kpi-label">Avail HH</div><div class="kpi-value">${(s.avail_hh/1e6).toFixed(2)}M</div><div class="kpi-sub">EBH available households</div></div>
  `;
}
function renderWoW(){
  const snaps = DATA.snapshots, lbls = DATA.snap_labels;
  const totals  = snaps.map(k=>DATA.summary[k].total);
  const valids  = snaps.map(k=>DATA.summary[k].valid_ebh);
  const ofs     = snaps.map(k=>DATA.summary[k].ofs_count/1e6);
  const hh      = snaps.map(k=>DATA.summary[k].avail_hh/1e6);

  Plotly.newPlot('chrt-wow-sites',[
    {x:lbls,y:totals,name:'Total Sites',type:'bar',marker:{color:'#0d6efd'}},
    {x:lbls,y:valids,name:'Valid EBH',type:'bar',marker:{color:'#198754'}},
  ],mkLayout({barmode:'group',yaxis:{title:'Sites',gridcolor:'#2a3b50'},legend:{orientation:'h',y:1.1}}),{responsive:true});

  Plotly.newPlot('chrt-wow-ofs',[
    {x:lbls,y:ofs,name:'OFS Count (M)',type:'scatter',mode:'lines+markers',
     line:{color:'#fd7e14',width:2},marker:{size:7}},
  ],mkLayout({yaxis:{title:'OFS (Millions)',gridcolor:'#2a3b50'}}),{responsive:true});

  Plotly.newPlot('chrt-wow-hh',[
    {x:lbls,y:hh,name:'Avail HH (M)',type:'scatter',mode:'lines+markers',
     line:{color:'#6f42c1',width:2},marker:{size:7,color:'#6f42c1'}},
  ],mkLayout({yaxis:{title:'Households (M)',gridcolor:'#2a3b50'}}),{responsive:true});
}
function renderClosedPie(){
  const s = DATA.summary[_sumSnap];
  const labels = Object.keys(s.closed_reasons);
  const values = Object.values(s.closed_reasons);
  if(!labels.length){ Plotly.newPlot('chrt-closed-pie',[],mkLayout({}),{responsive:true}); return; }
  Plotly.newPlot('chrt-closed-pie',[{
    type:'pie', labels, values,
    marker:{colors:['#dc3545','#fd7e14','#6f42c1','#ffc107']},
    hole:0.4, textfont:{color:'#fff',size:11},
  }],mkLayout({margin:{l:10,r:10,t:10,b:10},showlegend:true,
    legend:{orientation:'v',x:1.0,y:0.5,bgcolor:'#0d1520',bordercolor:'#2a3b50',borderwidth:1,font:{size:10}}}),{responsive:true});
}

// ═══ BANDWIDTH ═══════════════════════════════════════════════════════════════
let _bwSnap = DATA.latest;
function initBandwidth(){
  snapBtns('bw-snap-btns', k=>{ _bwSnap=k; renderBwCharts(); }, DATA.snapshots.length-1);
  renderBwCharts();
  renderBwTrend();
}
function renderBwCharts(){
  const d  = DATA.bandwidth[_bwSnap];
  const bw = DATA.bw_groups;
  Plotly.newPlot('chrt-bw-count',[
    {x:bw, y:d.open,  name:'Open',   type:'bar', marker:{color:'#198754'}},
    {x:bw, y:d.counts.map((t,i)=>t-d.open[i]), name:'Closed', type:'bar', marker:{color:'#dc3545'}},
  ],mkLayout({barmode:'stack',yaxis:{title:'Sites',gridcolor:'#2a3b50'},
    legend:{orientation:'h',y:1.1}}),{responsive:true});
  Plotly.newPlot('chrt-bw-ofs',[
    {x:bw, y:d.ofs, type:'bar',
     marker:{color:BW_COLORS}, text:d.ofs.map(v=>(v/1e6).toFixed(2)+'M'),
     textposition:'outside', textfont:{size:10,color:'#9ab'}},
  ],mkLayout({yaxis:{title:'OFS Count',gridcolor:'#2a3b50'},showlegend:false}),{responsive:true});
}
function renderBwTrend(){
  const snaps = DATA.snapshots, lbls = DATA.snap_labels;
  const traces = DATA.bw_groups.map((g,i)=>({
    x: lbls,
    y: snaps.map(k=>DATA.bandwidth[k].counts[i]),
    name: g, type:'scatter', mode:'lines+markers',
    line:{color:BW_COLORS[i],width:2}, marker:{size:6},
  }));
  Plotly.newPlot('chrt-bw-trend', traces,
    mkLayout({yaxis:{title:'Site Count',gridcolor:'#2a3b50'},legend:{orientation:'h',y:1.05}}),
    {responsive:true});
}

// ═══ UTILIZATION ════════════════════════════════════════════════════════════
let _utSnap = DATA.latest;
function initUtilization(){
  snapBtns('util-snap-btns', k=>{ _utSnap=k; renderUtilCharts(); }, DATA.snapshots.length-1);
  renderUtilCharts();
  buildHiUtilTable();
  // BW filter
  const sel = document.getElementById('hi-bw-filter');
  DATA.bw_groups.forEach(g=>{ const o=document.createElement('option'); o.value=g; o.textContent=g; sel.appendChild(o); });
}
function renderUtilCharts(){
  const d = DATA.utilization[_utSnap];
  Plotly.newPlot('chrt-ebh-util',[{
    x:DATA.ebh_util_groups, y:d.ebh_counts, type:'bar',
    marker:{color:EU_COLORS},
  }],mkLayout({yaxis:{title:'Sites',gridcolor:'#2a3b50'},showlegend:false,
    xaxis:{title:'EBH Utilization %',gridcolor:'#2a3b50'}}),{responsive:true});
  Plotly.newPlot('chrt-ran-util',[{
    x:DATA.ran_util_groups, y:d.ran_counts, type:'bar',
    marker:{color:'#6f42c1'},
  }],mkLayout({yaxis:{title:'Sites',gridcolor:'#2a3b50'},showlegend:false,
    xaxis:{title:'RAN Used %',gridcolor:'#2a3b50'}}),{responsive:true});
}
let _hiRows = [];
function buildHiUtilTable(){
  _hiRows = DATA.high_util;
  filterHiUtil();
}
function filterHiUtil(){
  const q  = document.getElementById('hi-search').value.toLowerCase();
  const bw = document.getElementById('hi-bw-filter').value;
  const rows = _hiRows.filter(r=>{
    const txt = [r['Fuze Site ID'],r['Site Name'],r['CMA'],r['Sub Market']].join(' ').toLowerCase();
    return (!q||txt.includes(q)) && (!bw||(r['Bandwidth Group']||'')==bw);
  });
  const tbody = document.getElementById('hi-tbl-body');
  tbody.innerHTML = rows.map(r=>`<tr>
    ${td(r['Fuze Site ID'])}${td(r['Site Name']||'—')}${td(r['CMA']||'—')}${td(r['Sub Market']||'—')}
    <td data-val="${r['EBH Usage % Group']||''}">${bwBadge(r['EBH Usage % Group'])}</td>
    ${td((r['RAN Used Percent']||0).toFixed(1)+'%','right')}
    <td data-val="${r['Bandwidth Group']||''}">${bwBadge(r['Bandwidth Group'])}</td>
    ${td(fmt(r['OFS Count']),'right')}
    <td data-val="${r['Open/Closed Status']||''}">${stBadge(r['Open/Closed Status'])}</td>
  </tr>`).join('');
}

// ═══ SUB MARKET ══════════════════════════════════════════════════════════════
let _smSnap = DATA.latest;
function initSubmarket(){
  snapBtns('sm-snap-btns', k=>{ _smSnap=k; renderSmBar(); renderSmTbl(); }, DATA.snapshots.length-1);
  renderSmBar();
  renderSmTbl();
}
function renderSmBar(){
  const rows = DATA.submarket[_smSnap];
  const disp = rows.slice(0,19);
  Plotly.newPlot('chrt-sm-bar',[
    {y:disp.map(r=>r.name), x:disp.map(r=>r.open),  name:'Open',
     type:'bar', orientation:'h', marker:{color:'#198754'}},
    {y:disp.map(r=>r.name), x:disp.map(r=>r.total-r.open), name:'Closed',
     type:'bar', orientation:'h', marker:{color:'#dc3545'}},
  ],mkLayout({barmode:'stack',
    xaxis:{title:'Site Count',gridcolor:'#2a3b50'},
    yaxis:{gridcolor:'#2a3b50',autorange:'reversed'},
    margin:{l:220,r:20,t:55,b:50},
    legend:{orientation:'h',y:1.12,x:0}}),{responsive:true});
}
function renderSmTbl(){
  const rows = DATA.submarket[_smSnap];
  const tbody = document.getElementById('sm-tbl-body');
  tbody.innerHTML = rows.map(r=>`<tr style="cursor:pointer" onclick="showCma('${r.name.replace(/'/g,"\\'")}')">
    <td data-val="${r.name}">${r.name}</td>
    ${td(fmt(r.total),'right')}${td(fmt(r.open),'right')}
    ${td(fmt(r.total-r.open),'right')}${td(fmt(r.valid_ebh),'right')}
    ${td(fmt(r.ofs_count),'right')}${td(fmt(r.avail_hh),'right')}
    ${td(r.avg_7d?(r.avg_7d*100).toFixed(3)+'%':'—','right')}
    ${td(r.avg_ran!=null?r.avg_ran.toFixed(1)+'%':'—','right')}
  </tr>`).join('');
}
function filterSmTbl(){
  const q = document.getElementById('sm-search').value.toLowerCase();
  document.querySelectorAll('#sm-tbl tbody tr').forEach(r=>{
    r.style.display = r.cells[0]?.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
function showCma(smName){
  const cmas = (DATA.cma[_smSnap]||{})[smName];
  if(!cmas||!cmas.length) return;
  document.getElementById('cma-title').textContent = 'CMA Drill-Down: ' + smName;
  document.getElementById('cma-section').style.display = '';
  const tbody = document.getElementById('cma-tbl-body');
  tbody.innerHTML = cmas.map(r=>`<tr>
    ${td(r.name)}${td(fmt(r.total),'right')}${td(fmt(r.open),'right')}
    ${td(fmt(r.ofs_count),'right')}${td(fmt(r.avail_hh),'right')}
    ${td(r.avg_ran!=null?r.avg_ran.toFixed(1)+'%':'—','right')}
  </tr>`).join('');
  document.getElementById('cma-section').scrollIntoView({behavior:'smooth',block:'nearest'});
}

// ═══ TRANSPORT ═══════════════════════════════════════════════════════════════
let _ttSnap = DATA.latest;
function initTransport(){
  snapBtns('tt-snap-btns', k=>{ _ttSnap=k; renderTtCharts(); renderTtTbl(); }, DATA.snapshots.length-1);
  renderTtCharts();
  renderTtTbl();
}
function renderTtCharts(){
  const rows = DATA.transport[_ttSnap];
  const colors = ['#0d6efd','#198754','#fd7e14','#6f42c1','#dc3545','#20c997','#ffc107','#e83e8c'];
  Plotly.newPlot('chrt-tt-pie',[{
    type:'pie', labels:rows.map(r=>r.type), values:rows.map(r=>r.count),
    marker:{colors:colors.slice(0,rows.length)},
    hole:0.35, textfont:{color:'#fff',size:11},
  }],mkLayout({margin:{l:10,r:10,t:10,b:10},
    legend:{orientation:'v',x:1.0,y:0.5,bgcolor:'#0d1520',bordercolor:'#2a3b50',borderwidth:1,font:{size:10}}}),{responsive:true});
  Plotly.newPlot('chrt-tt-ofs',[{
    y:rows.map(r=>r.type), x:rows.map(r=>r.ofs_count), type:'bar',
    orientation:'h', marker:{color:colors.slice(0,rows.length)},
  }],mkLayout({xaxis:{title:'OFS Count',gridcolor:'#2a3b50'},
    yaxis:{gridcolor:'#2a3b50',autorange:'reversed'},
    showlegend:false, margin:{l:120,r:20,t:30,b:50}}),{responsive:true});
}
function renderTtTbl(){
  const rows = DATA.transport[_ttSnap];
  const tbody = document.getElementById('tt-tbl-body');
  tbody.innerHTML = rows.map(r=>`<tr>
    ${td(r.type)}${td(fmt(r.count),'right')}${td(r.pct+'%','right')}
    ${td(fmt(r.open),'right')}${td(fmt(r.valid_ebh),'right')}
    ${td(fmt(r.ofs_count),'right')}${td(fmt(r.avail_hh),'right')}
    ${td(r.avg_bw_gbps,'right')}
  </tr>`).join('');
}

// ═══ CLOSED SITES ═════════════════════════════════════════════════════════════
let _clSnap = DATA.latest;
let _clRows = [];
function initClosed(){
  snapBtns('cl-snap-btns', k=>{ _clSnap=k; renderClKpi(); buildClTbl(); }, DATA.snapshots.length-1);
  const sel = document.getElementById('cl-bw-filter');
  DATA.bw_groups.forEach(g=>{ const o=document.createElement('option'); o.value=g; o.textContent=g; sel.appendChild(o); });
  renderClKpi();
  buildClTbl();
}
function renderClKpi(){
  const s = DATA.summary[_clSnap];
  const r = s.closed_reasons;
  document.getElementById('cl-kpi').innerHTML = `
    <div class="kpi c-red"><div class="kpi-label">Total Closed</div><div class="kpi-value">${fmt(s.closed)}</div></div>
    <div class="kpi c-orange"><div class="kpi-label">BW &lt; 480M</div><div class="kpi-value">${fmt(r['Closed - Bandwidth<480']||0)}</div></div>
    <div class="kpi c-purple"><div class="kpi-label">85% Util</div><div class="kpi-value">${fmt(r['Closed - 85% util for 4 weeks']||0)}</div></div>
    <div class="kpi c-blue"><div class="kpi-label">No Hub Cap</div><div class="kpi-value">${fmt(r['Closed - No Hub capacity']||0)}</div></div>
  `;
}
function buildClTbl(){
  _clRows = DATA.closed[_clSnap] || [];
  filterClosed();
}
function filterClosed(){
  const q  = document.getElementById('cl-search').value.toLowerCase();
  const rs = document.getElementById('cl-reason-filter').value;
  const bw = document.getElementById('cl-bw-filter').value;
  const rows = _clRows.filter(r=>{
    const txt = [r['Fuze Site ID'],r['Site Name'],r['CMA'],r['Sub Market']].join(' ').toLowerCase();
    const st  = (r['Open/Closed Status']||'');
    return (!q||txt.includes(q)) && (!rs||st.includes(rs)) && (!bw||(r['Bandwidth Group']||'')==bw);
  });
  const tbody = document.getElementById('cl-tbl-body');
  tbody.innerHTML = rows.map(r=>`<tr>
    ${td(r['Fuze Site ID'])}${td(r['Site Name']||'—')}
    ${td(r['CMA']||'—')}${td(r['Sub Market']||'—')}
    <td data-val="${r['Open/Closed Status']||''}">${stBadge(r['Open/Closed Status'])}</td>
    <td data-val="${r['Bandwidth Group']||''}">${bwBadge(r['Bandwidth Group'])}</td>
    ${td(r['Bandwidth (mbps)'],'right')}${td(r['Transport Type']||'—')}
    ${td(fmt(r['OFS Count']),'right')}
    <td>${r['Valid EBH']?'<span class="badge b-green">Yes</span>':'<span class="badge b-red">No</span>'}</td>
  </tr>`).join('');
}

// ═══ HUB-SPOKE ══════════════════════════════════════════════════════════════
let _hubRows = [];
function initHubSpoke(){
  const hubs   = Object.keys(DATA.hub_detail);
  const spokes = Object.keys(DATA.spoke_to_hub);
  const avg    = spokes.length / (hubs.length||1);
  document.getElementById('hs-kpi-hubs').textContent   = fmt(hubs.length);
  document.getElementById('hs-kpi-spokes').textContent = fmt(spokes.length);
  document.getElementById('hs-kpi-avg').textContent    = avg.toFixed(1);

  // SM filter
  const sel = document.getElementById('hs-sm-filter');
  const sms = [...new Set(Object.values(DATA.hub_detail).map(h=>h.sm))].filter(s=>s&&s!='?').sort();
  sms.forEach(s=>{ const o=document.createElement('option'); o.value=s; o.textContent=s; sel.appendChild(o); });

  _hubRows = Object.entries(DATA.hub_detail).map(([id,h])=>({id,...h}));
  _hubRows.sort((a,b)=>b.spokes-a.spokes);
  renderHubTbl(_hubRows);
}
function renderHubTbl(rows){
  const tbody = document.getElementById('hub-tbl-body');
  tbody.innerHTML = rows.map(r=>`<tr style="cursor:pointer" onclick="hsLookupId('${r.id}')">
    <td data-val="${r.id}">${r.id}</td>
    ${td(r.sm)}${td(r.cma)}
    <td data-val="${r.bw||''}">${bwBadge(r.bw)}</td>
    <td data-val="${r.status||''}">${stBadge(r.status)}</td>
    ${td(fmt(r.spokes),'right')}${td(r.transport||'—')}
    <td>${r.valid_ebh?'<span class="badge b-green">Yes</span>':'<span class="badge b-red">No</span>'}</td>
  </tr>`).join('');
}
function filterHubTbl(){
  const q  = document.getElementById('hs-hub-search').value.toLowerCase();
  const sm = document.getElementById('hs-sm-filter').value;
  const rows = _hubRows.filter(r=>{
    const txt = (r.id+' '+r.sm+' '+r.cma).toLowerCase();
    return (!q||txt.includes(q)) && (!sm||r.sm===sm);
  });
  renderHubTbl(rows);
}
function hsLookupId(id){ document.getElementById('hs-search').value=id; hsLookup(id); }
function hsLookup(val){
  const id = (val||'').trim();
  const out = document.getElementById('hs-result');
  if(!id){ out.innerHTML=''; return; }

  // Check if it's a hub
  const hub = DATA.hub_detail[id];
  const spokes = DATA.hub_to_spokes[id];
  // Check if it's a spoke
  const hubOfThis = DATA.spoke_to_hub[id];

  // Check map data for basic site info
  const M = DATA.map_data;
  const idx = M.ids.indexOf(parseInt(id));
  const hasSiteData = idx >= 0;
  const geo = hasSiteData ? {
    lat: M.lats[idx], lng: M.lngs[idx],
    bw: DATA.bw_groups[M.bw[idx]],
    status: DATA.status_labels[M.st[idx]],
    ofs: M.ofs[idx],
    eu: DATA.ebh_util_groups[M.eu[idx]],
    sm: DATA.map_data.sub_markets[M.sm[idx]],
    cma: DATA.map_data.cma_list[M.cma[idx]],
    on_air: M.on_air[idx],
  } : null;

  if(!hub && !hubOfThis && !hasSiteData){
    out.innerHTML = '<div style="color:#fd7e14;padding:8px">Fuze Site ID not found in latest snapshot.</div>';
    return;
  }

  let html = '<div class="hub-card">';
  html += `<h4>Fuze Site ID: ${id}</h4>`;
  if(geo){
    html += `<div class="hub-meta">
      <span><b>BW Group:</b> ${bwBadge(geo.bw)}</span>
      <span><b>Status:</b> ${stBadge(geo.status)}</span>
      <span><b>Sub Market:</b> ${geo.sm||'—'}</span>
      <span><b>CMA:</b> ${geo.cma||'—'}</span>
      <span><b>OFS Count:</b> ${fmt(geo.ofs)}</span>
      <span><b>EBH Util:</b> ${geo.eu||'—'}</span>
      <span><b>On Air Date:</b> ${geo.on_air||'Not on air'}</span>
      <span><b>Lat/Lng:</b> ${geo.lat}, ${geo.lng}</span>
    </div>`;
  }
  if(hub && spokes){
    html += `<div style="color:#ffc107;font-size:12px;margin-bottom:6px">&#x2B50; HUB SITE — ${spokes.length} spoke site(s) connected</div>`;
    html += `<details><summary style="cursor:pointer;font-size:12px;color:#60a5fa">Show ${spokes.length} spoke site(s)</summary>`;
    html += `<div style="padding:8px 0;font-size:11px;color:#9ab;font-family:monospace">${spokes.join(', ')}</div></details>`;
  }
  if(hubOfThis){
    const hd = DATA.hub_detail[hubOfThis];
    html += `<div style="color:#60a5fa;font-size:12px;margin-top:6px">&#x1F517; SPOKE SITE — Hub: <b>${hubOfThis}</b>`;
    if(hd) html += ` (${hd.sm||'?'} | ${bwBadge(hd.bw)} | ${stBadge(hd.status)} | ${hd.spokes} spokes total)`;
    html += `</div>`;
  }
  if(!hub && !hubOfThis) html += `<div style="color:#7a8ba0;font-size:12px;margin-top:6px">No hub-spoke relationship on file for this site.</div>`;
  html += '</div>';
  out.innerHTML = html;
}

// ═══ MAP ═════════════════════════════════════════════════════════════════════
function initMapControls(){
  const sel = document.getElementById('map-sm-filter');
  DATA.map_data.sub_markets.forEach((sm,i)=>{
    const o = document.createElement('option'); o.value=i; o.textContent=sm; sel.appendChild(o);
  });
}
initMapControls();

let _mapRendered = false;
function renderMap(){
  const mode    = document.getElementById('map-color-mode').value;
  const smFilter = parseInt(document.getElementById('map-sm-filter').value);
  const M = DATA.map_data;
  const n = M.ids.length;

  // Build group arrays
  let groups, groupColors, groupLabels;
  if(mode==='bw'){
    groups=DATA.bw_groups; groupColors=BW_COLORS; groupLabels=DATA.bw_groups;
  } else if(mode==='status'){
    groups=DATA.status_labels; groupColors=ST_COLORS; groupLabels=DATA.status_labels;
  } else {
    groups=DATA.ebh_util_groups; groupColors=EU_COLORS; groupLabels=DATA.ebh_util_groups;
  }

  // Legend
  const legEl = document.getElementById('map-legend');
  legEl.innerHTML = groups.map((g,i)=>
    `<div class="leg-item"><div class="leg-dot" style="background:${groupColors[i]}"></div>${g}</div>`
  ).join('');

  const traces = [];
  groups.forEach((g, gi)=>{
    const lats=[],lons=[],texts=[],ids=[];
    for(let i=0;i<n;i++){
      if(smFilter>=0 && M.sm[i]!==smFilter) continue;
      let gi2;
      if(mode==='bw')     gi2=M.bw[i];
      else if(mode==='status') gi2=M.st[i];
      else gi2=M.eu[i];
      if(gi2!==gi) continue;
      lats.push(M.lats[i]);
      lons.push(M.lngs[i]);
      ids.push(M.ids[i]);
      const cma = M.cma[i]>=0 ? M.cma_list[M.cma[i]] : '';
      const oa  = M.on_air[i] || 'Not on air';
      texts.push(`ID: ${M.ids[i]}<br>${cma}<br>OFS: ${M.ofs[i]}<br>On Air: ${oa}`);
    }
    if(lats.length===0) return;
    traces.push({
      type:'scattergeo', mode:'markers', name:g,
      lat:lats, lon:lons, text:texts,
      hovertemplate:'%{text}<extra></extra>',
      marker:{size:4, color:groupColors[gi], opacity:0.75},
    });
  });

  const layout = {
    paper_bgcolor:'#1a2535',
    font:{color:'#9ab',size:11},
    margin:{l:0,r:0,t:0,b:0},
    legend:{bgcolor:'#0d1520',bordercolor:'#2a3b50',borderwidth:1,x:0.01,y:0.99,font:{size:10}},
    geo:{
      scope:'usa',
      bgcolor:'#0d1520',
      landcolor:'#1a2535',
      coastlinecolor:'#2a3b50',
      countrycolor:'#2a3b50',
      showlakes:false,
      subunitcolor:'#2a3b50',
      projection:{type:'albers usa'},
    },
  };
  Plotly.newPlot('chrt-map', traces, layout, {responsive:true});
}

// ── Init summary on load ──────────────────────────────────────────────────────
initSummary();
rendered['summary'] = true;
</script>
</body>
</html>
"""

# ─── Inject data + Plotly ──────────────────────────────────────────────────────
print("Generating HTML...")
if PLOTLY_JS:
    plotly_tag = f"<script>{PLOTLY_JS}</script>"
else:
    plotly_tag = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'

html = HTML.replace("DATA_PLACEHOLDER", DATA_JSON).replace("PLOTLY_PLACEHOLDER", plotly_tag)
OUTPUT.write_text(html, encoding="utf-8")
sz = OUTPUT.stat().st_size / 1024 / 1024
print(f"\nOutput: {OUTPUT}")
print(f"File size: {sz:.1f} MB")
print("Done!")
