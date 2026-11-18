#!/usr/bin/env python3
"""test_p67_frontend_auth_api.py — P67 Frontend auth API hardening (audit V1.0 A1).

Audit V1.0 P0-A1 : « frontend index.html court-circuite l'API auth (localStorage + btoa pwd) ».

Vérification P67 :
  1. En production (HTTP), doLogin/doSignup sont OVERRIDÉS par l'adaptateur API (apiFetch /api/auth/login
     + /api/auth/register + JWT). Le fallback localStorage n'est actif qu'en file:// (preview standalone).
  2. Le fallback file:// ne stocke PLUS de mot de passe (btoa retiré — security smell éliminé).
  3. Mode preview file:// honnête : auth par email seulement (pas de fausse sécurité base64).

Coverage :
  A. Override API HTTP actif :
    1. index.html contient doLogin=async...apiFetch('/api/auth/login').
    2. index.html contient doSignup=async...apiFetch('/api/auth/register').
    3. index.html contient apiSetTok (JWT storage localStorage 'revpulse_jwt').
    4. index.html contient if(location.protocol.startsWith('http')) (gate override).
  B. Security smell éliminé :
    5. index.html ne contient AUCUN btoa( (pas de stockage mot de passe base64).
    6. index.html ne contient AUCUN atob( (pas de décodage base64).
    7. Le fallback doLogin (function declaration) ne compare plus x.pwd===btoa(pwd).
  C. Gate preview vs production :
    8. Le fallback file:// est commenté "preview" / "P67" (marquage honnête).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "frontend" / "public" / "index.html"
content = HTML.read_text()

passed = failed = 0


def ck(cond, label):
    global passed, failed
    print(f"  {'✓' if cond else '✗'} {label}")
    if cond:
        passed += 1
    else:
        failed += 1


def main():
    print("=== test_p67_frontend_auth_api.py — P67 Frontend auth API hardening (A1) ===\n")

    print("--- A. Override API HTTP actif ---")
    ck(bool(re.search(r"doLogin\s*=\s*async.*apiFetch\(['\"]/api/auth/login['\"]", content)),
       "index.html : doLogin override → apiFetch /api/auth/login (production HTTP)")
    ck(bool(re.search(r"doSignup\s*=\s*async.*apiFetch\(['\"]/api/auth/register['\"]", content)),
       "index.html : doSignup override → apiFetch /api/auth/register")
    ck("apiSetTok" in content and "revpulse_jwt" in content,
       "index.html : apiSetTok + JWT storage localStorage 'revpulse_jwt'")
    ck("location.protocol.startsWith('http')" in content,
       "index.html : gate if(location.protocol.startsWith('http')) (override actif en HTTP uniquement)")

    print("\n--- B. Security smell éliminé (btoa/atoba retirés) ---")
    ck("btoa(" not in content, "index.html : AUCUN btoa( (pas de stockage mot de passe base64)")
    ck("atob(" not in content, "index.html : AUCUN atob( (pas de décodage base64)")
    # Le fallback doLogin (function declaration, non-override) ne compare plus pwd via btoa
    fallback_login = re.search(r"function doLogin\(\)\{.*?\n\}", content, re.DOTALL)
    ck(fallback_login is not None and "btoa" not in fallback_login.group(0),
       "fallback doLogin (file://) ne compare plus x.pwd===btoa(pwd)")
    fallback_signup = re.search(r"function doSignup\(\)\{.*?\n\}", content, re.DOTALL)
    ck(fallback_signup is not None and "btoa" not in fallback_signup.group(0),
       "fallback doSignup (file://) ne stocke plus pwd:btoa(pwd)")

    print("\n--- C. Gate preview vs production (marquage honnête) ---")
    # P67 comment marker dans le fallback doLogin
    ck("P67" in content and "preview" in content.lower(),
       "index.html : marquage P67/preview (mode file:// honnête — pas de fausse sécurité)")
    # Le fallback ne stocke aucun champ pwd dans les users localStorage
    ck("pwd:btoa" not in content and "pwd: btoa" not in content,
       "index.html : aucun champ 'pwd:btoa' stocké dans localStorage users")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
