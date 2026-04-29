#!/usr/bin/env python3
"""
Switch Decom Dashboard — Local Flask Server
Run: python switch_decom_server.py
Opens: http://localhost:5000
"""
import sys, threading, webbrowser, warnings, os
from pathlib import Path
from datetime import datetime
import oracledb
from flask import Flask, jsonify, request, send_file, abort
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

# Load credentials from .env (never commit .env to git)
load_dotenv(Path(__file__).parent / ".env")

DSN  = os.environ["ORACLE_DSN"]
USER = os.environ["ORACLE_USER"]
PASS = os.environ["ORACLE_PASS"]
PORT = 5000
HTML = Path(r"C:\Users\v296938\Desktop\switch_decom_dashboard.html")

app = Flask(__name__)

# ─── Oracle helpers ───────────────────────────────────────────────────────────
def get_conn():
    return oracledb.connect(user=USER, password=PASS, dsn=DSN)

def init_tables():
    conn = get_conn()
    cur  = conn.cursor()
    tables = {
        "SD_NOTES": """
            CREATE TABLE tableau_user.SD_NOTES (
                CLLI        VARCHAR2(12)  PRIMARY KEY,
                NOTE_TEXT   VARCHAR2(4000),
                FLAGGED     CHAR(1)       DEFAULT 'N',
                UPDATED_DT  DATE          DEFAULT SYSDATE,
                UPDATED_BY  VARCHAR2(50)  DEFAULT USER
            )""",
    }
    for name, ddl in tables.items():
        try:
            cur.execute(ddl)
            conn.commit()
            print(f"  Created TABLEAU_USER.{name}")
        except oracledb.DatabaseError as e:
            if "ORA-00955" in str(e):   # already exists
                print(f"  TABLEAU_USER.{name} already exists")
            else:
                print(f"  Warning creating {name}: {e}")
    conn.close()

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if not HTML.exists():
        abort(404, "Dashboard HTML not found. Run generate_switch_decom_html.py first.")
    return send_file(str(HTML))

@app.route("/api/notes/<clli>", methods=["GET"])
def get_note(clli):
    clli = clli.upper().strip()
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute(
            "SELECT NOTE_TEXT, FLAGGED, UPDATED_DT FROM tableau_user.SD_NOTES WHERE CLLI = :1",
            [clli]
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return jsonify({
                "clli":    clli,
                "note":    row[0] or "",
                "flagged": row[1] == "Y",
                "updated": str(row[2]) if row[2] else ""
            })
        return jsonify({"clli": clli, "note": "", "flagged": False, "updated": ""})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/notes/<clli>", methods=["POST"])
def save_note(clli):
    clli = clli.upper().strip()
    data    = request.get_json(force=True)
    note    = data.get("note", "").strip()
    flagged = "Y" if data.get("flagged", False) else "N"
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            MERGE INTO tableau_user.SD_NOTES t
            USING DUAL ON (t.CLLI = :clli)
            WHEN MATCHED THEN
                UPDATE SET NOTE_TEXT=:note, FLAGGED=:flagged,
                           UPDATED_DT=SYSDATE, UPDATED_BY=USER
            WHEN NOT MATCHED THEN
                INSERT (CLLI, NOTE_TEXT, FLAGGED, UPDATED_DT, UPDATED_BY)
                VALUES (:clli, :note, :flagged, SYSDATE, USER)
        """, {"clli": clli, "note": note, "flagged": flagged})
        conn.commit()
        conn.close()
        return jsonify({"status": "saved", "clli": clli})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/flags", methods=["GET"])
def get_flags():
    """Return all flagged CLLIs so the dashboard can highlight them."""
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("SELECT CLLI FROM tableau_user.SD_NOTES WHERE FLAGGED='Y'")
        flags = [r[0] for r in cur.fetchall()]
        conn.close()
        return jsonify({"flagged": flags})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/circuits/<clli>", methods=["GET"])
def get_circuits(clli):
    """Return circuit-level rows for a CLLI from NT_DECOM_CIRCUITS_SOURCE (max 2000)."""
    clli = clli.upper().strip()
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT
                NVL(WTN, POTS_CKT_ID)       AS IDENTIFIER,
                BILLING_LINE_USOC            AS USOC,
                BILLING_USOC_DESC            AS USOC_DESC,
                NVL(MIGRATION_COMPLETE,'N')  AS STATUS,
                TO_CHAR(MIGRATION_COMPLETE_DATE,'YYYY-MM-DD') AS MIG_DATE,
                TO_CHAR(DECOM_WAVE_ID)       AS WAVE,
                NVL(COPPER_FIBER_IND,'')     AS CF,
                CUSTOMER_NAME                AS CUSTOMER,
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
                    ELSE 'Other / Unknown'
                END AS CKT_CAT
            FROM VNADSPRD.NT_DECOM_CIRCUITS_SOURCE
            WHERE UPPER(CLLI_CD) = :1
            ORDER BY MIGRATION_COMPLETE DESC, BILLING_USOC_DESC
            FETCH FIRST 2000 ROWS ONLY
        """, [clli])
        rows = cur.fetchall()
        conn.close()
        return jsonify({
            "clli":  clli,
            "total": len(rows),
            "rows":  [[str(v) if v is not None else "" for v in r] for r in rows]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/notes", methods=["GET"])
def get_all_notes():
    """Return all CLLIs that have notes or flags."""
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT CLLI, NOTE_TEXT, FLAGGED, UPDATED_DT
            FROM tableau_user.SD_NOTES
            ORDER BY UPDATED_DT DESC
        """)
        rows = cur.fetchall()
        conn.close()
        return jsonify([{
            "clli":    r[0],
            "note":    r[1] or "",
            "flagged": r[2] == "Y",
            "updated": str(r[3]) if r[3] else ""
        } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Startup ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  Switch Decom Dashboard — Local Server")
    print("=" * 55)
    print(f"\nConnecting to Oracle NARPROD...")
    try:
        conn = get_conn()
        conn.close()
        print("  Oracle connection OK")
    except Exception as e:
        print(f"  Oracle connection FAILED: {e}")
        sys.exit(1)

    print("\nInitializing annotation tables...")
    init_tables()

    print(f"\nStarting server at http://localhost:{PORT}")
    print("Press Ctrl+C to stop.\n")

    # Open browser after short delay
    def open_browser():
        import time; time.sleep(1.2)
        webbrowser.open(f"http://localhost:{PORT}")
    threading.Thread(target=open_browser, daemon=True).start()

    app.run(host="localhost", port=PORT, debug=False)
