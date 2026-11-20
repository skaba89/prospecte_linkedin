#!/usr/bin/env python3
"""test_p84_xlsx_export.py — Export Excel (.xlsx) des contacts (P84).

Démarre uvicorn, login démo, appelle GET /tenants/{tid}/analytics/export.xlsx et valide :
  1. Statut 200 + content-type xlsx + content-disposition .xlsx.
  2. Body est un .xlsx valide (parsé par openpyxl — lève si corrompu).
  3. En-tête = 11 colonnes attendues (id, first_name, ..., created_at).
  4. Nombre de lignes = nombre de contacts du tenant (cohérence DB).
  5. Freeze pane A2 + autofilter + en-tête stylé (gras + fond bleu).
  6. RBAC : sans token → 401 (données PII contacts).
  7. Régression : export CSV toujours fonctionnel (même colonnes, même count).

Le endpoint xlsx partage les helpers _EXPORT_COLS + _contact_row avec le CSV
(source unique de vérité) → les deux exports sont cohérents par construction.
"""
import io
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
passed = failed = 0
SERVER_PROC = None


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def start_server():
    global SERVER_PROC
    env = dict(os.environ)
    env["REVPULSE_DATABASE_URL"] = "sqlite:///./revpulse_p84.db"
    env["REVPULSE_ENV"] = "test"
    env["WEBHOOK_SSRF_ALLOW_PRIVATE"] = "true"
    env["REVPULSE_FRONTEND_DIR"] = str(ROOT / "frontend" / "public")
    subprocess.run("pkill -f 'uvicorn app.main:app' 2>/dev/null || true", shell=True)
    subprocess.run("pkill -f 'uvicorn' 2>/dev/null || true", shell=True)
    time.sleep(2)
    subprocess.run(f"rm -f {ROOT}/backend/revpulse_p84.db", shell=True)
    SERVER_PROC = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(ROOT / "backend"), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        try:
            urllib.request.urlopen(URL + "/api/health", timeout=2); return True
        except Exception:
            time.sleep(1)
    return False


def call(method, path, token=None, raw=False):
    req = urllib.request.Request(URL + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
            return r.status, r.headers, data if raw else data.decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read() if raw else e.read().decode()


def main():
    global passed, failed
    import openpyxl

    if not start_server():
        print("✗ Serveur n'a pas démarré"); sys.exit(1)
    print(f"✓ Serveur démarré sur {URL}\n")

    # --- Login démo + fetch tenants séparément (comme le frontend) ---
    st, hdr, body = call("POST", "/api/auth/demo")
    demo = json.loads(body)
    tok = demo.get("token")
    ck(bool(tok), f"login démo → token (got {bool(tok)})")
    # /api/auth/demo renvoie {token, user, verify_token} — pas de tenants inline.
    # On fetch les tenants via GET /tenants (même flow que sequences.html L148).
    st, hdr, body = call("GET", "/api/tenants", token=tok)
    tenants = json.loads(body) if st == 200 else []
    ck(isinstance(tenants, list) and len(tenants) > 0, f"GET /tenants → liste non vide (got {len(tenants) if isinstance(tenants, list) else 'n/a'})")
    if not tenants:
        print("✗ Pas de tenants — abort"); sys.exit(1)
    tid = tenants[0]["id"]

    # --- Comptage contacts (référence DB via API CRM). limit max=200 (validation FastAPI).
    # response_model=list[ContactOut] → renvoie une liste plain, pas {items,total}.
    st, hdr, body = call("GET", f"/api/tenants/{tid}/crm/contacts?limit=200", token=tok)
    if st == 200:
        contacts_list = json.loads(body)
        total_contacts = len(contacts_list) if isinstance(contacts_list, list) else 0
    else:
        total_contacts = -1  # sentinelle : API CRM indisponible (skip cross-check)
        print(f"    ⚠ GET /crm/contacts statut {st} — cross-check DB désactivé")

    print(f"\n--- Endpoint xlsx (tid={tid}, {total_contacts} contacts) ---")

    # 1. Statut + content-type + content-disposition
    st, hdr, data = call("GET", f"/api/tenants/{tid}/analytics/export.xlsx", token=tok, raw=True)
    ck(st == 200, f"statut 200 (got {st})")
    ctype = hdr.get("content-type", "")
    ck("spreadsheetml" in ctype, f"content-type xlsx (got '{ctype}')")
    cdisp = hdr.get("content-disposition", "")
    ck(".xlsx" in cdisp and tid in cdisp, f"content-disposition .xlsx + tid (got '{cdisp}')")
    ck(len(data) > 100, f"body non vide (>100 bytes, got {len(data)})")

    # 2. xlsx valide (parsé par openpyxl — lève si corrompu)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=False)
        ws = wb.active
        xlsx_ok = True
    except Exception as e:
        xlsx_ok = False
        print(f"    → parse openpyxl échoué: {e}")
    ck(xlsx_ok, "body parsé par openpyxl (xlsx valide)")

    if xlsx_ok:
        # 3. En-tête = 11 colonnes attendues
        header = [c.value for c in ws[1]]
        expected = ["id", "first_name", "last_name", "email", "phone", "title",
                    "status", "source", "score", "linkedin_url", "created_at"]
        ck(header == expected, f"en-tête = 11 colonnes attendues (got {len(header)}: {header[:3]}...)")

        # 4. Nombre de lignes = contacts DB
        data_rows = ws.max_row - 1  # -1 pour l'en-tête
        ck(data_rows == total_contacts, f"lignes data = contacts DB ({data_rows} == {total_contacts})")

        # 5. Freeze pane + autofilter + en-tête stylé
        ck(ws.freeze_panes == "A2", f"freeze pane A2 (got {ws.freeze_panes})")
        ck(ws.auto_filter.ref is not None and ws.auto_filter.ref != "", f"autofilter activé (ref={ws.auto_filter.ref})")
        h1 = ws.cell(row=1, column=1)
        ck(h1.font.bold is True, "en-tête gras")
        ck(h1.fill.fgColor.rgb in ("FF1F4E78", "001F4E78", "1F4E78"), f"en-tête fond bleu (got {h1.fill.fgColor.rgb})")

    # 6. RBAC : sans token → 401 (données PII)
    st, _, _ = call("GET", f"/api/tenants/{tid}/analytics/export.xlsx")
    ck(st == 401, f"RBAC sans token → 401 (got {st})")

    # 7. Régression : CSV toujours fonctionnel + mêmes colonnes
    print("\n--- Régression CSV ---")
    st, hdr, csv_data = call("GET", f"/api/tenants/{tid}/analytics/export", token=tok)
    ck(st == 200, f"CSV export statut 200 (got {st})")
    ck("text/csv" in hdr.get("content-type", ""), "CSV content-type text/csv")
    # csv.writer utilise \r\n comme terminator → normaliser avant split
    csv_lines = [ln.rstrip("\r") for ln in csv_data.split("\n") if ln.strip()]
    csv_header = csv_lines[0].split(",") if csv_lines else []
    ck(len(csv_header) == 11, f"CSV même 11 colonnes (got {len(csv_header)})")
    ck(len(csv_lines) - 1 == total_contacts, f"CSV mêmes lignes que contacts DB ({len(csv_lines)-1} == {total_contacts})")

    # 8. Cohérence : xlsx + CSV ont exactement le même en-tête (helpers partagés)
    #    + invariant cross-format : même nombre de lignes data.
    if xlsx_ok:
        ck(header == csv_header, "xlsx + CSV : en-tête identique (helpers _EXPORT_COLS partagés)")
        ck(data_rows == len(csv_lines) - 1, f"invariant cross-format : xlsx rows ({data_rows}) == CSV rows ({len(csv_lines)-1})")

    if SERVER_PROC:
        SERVER_PROC.terminate(); SERVER_PROC.wait(timeout=5)
    subprocess.run("pkill -f 'uvicorn app.main:app' 2>/dev/null || true", shell=True)
    subprocess.run(f"rm -f {ROOT}/backend/revpulse_p84.db", shell=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
