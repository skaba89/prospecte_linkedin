#!/usr/bin/env python3
"""test_p35_v2_connecteurs.py — P35-V2 connecteur France Travail RÉEL (API Offres d'emploi v2).

Coverage (mock HTTP via unittest.mock — AUCUN réseau externe en CI) :
  1. not_configured : credentials absents → status "not_configured" + 0 postings (anti-faux-succès).
  2. OAuth token fetch : mock urlopen POST token → token caché + réutilisé (pas de 2e demande).
  3. search success : mock token + search API → postings mappés (title, company, location, contract,
     salary, posted_at, external_id, url, raw_data).
  4. search 0 résultats : mock API retourne {"resultats": []} → status "ok" + 0 postings (légitime).
  5. search 401 unauthorized : mock HTTPError 401 → status "unauthorized" + cache token invalidé.
  6. search error/timeout : mock URLError → status "error".
  7. Mapping helpers : _strip_html (balises → texte), _parse_salary (libellé → min/max),
     _parse_date_creation (epoch ms → datetime), _map_offre (JSON → JobPostingData), _build_params.
  8. Registry : get_connector("france_travail") → auth_method=oauth2 + configured reflete credentials.

Usage : python tests/test_p35_v2_connecteurs.py
"""
import os
import sys
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_ENV", "test")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def _mock_response(status_code: int, body: bytes):
    """Crée un mock context manager pour urllib.request.urlopen (resp.status + resp.read())."""
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.status = status_code
    m.read = MagicMock(return_value=body)
    return m


def main():
    # Import après setup env
    from app.services.job_connectors import france_travail as ft
    from app.services.job_connectors.base import SearchCriteria, JobPostingData
    from app.services.job_connectors.registry import get_connector, list_connectors
    from app.config import settings

    print("=== test_p35_v2_connecteurs.py — P35-V2 France Travail réel ===\n")

    # ── 1. not_configured (credentials absents) ───────────────────────────
    print("--- 1. not_configured (credentials absents) ---")
    # Reset credentials + token cache
    ft._TOKEN_CACHE["access_token"] = ""
    ft._TOKEN_CACHE["expires_at"] = 0.0
    with patch.object(settings, "france_travail_client_id", ""), \
         patch.object(settings, "france_travail_client_secret", ""):
        ck(not ft._is_configured(), "_is_configured()=False sans credentials")
        res = ft.search_france_travail(SearchCriteria(keywords="developer"))
        ck(res.status == "not_configured", f"status=not_configured (got {res.status})")
        ck(len(res.postings) == 0, "0 postings (anti-faux-succès)")
        ck("CLIENT_ID/SECRET absents" in (res.error or ""), f"error mentionne credentials absents")

    # ── 2. OAuth token fetch + cache ──────────────────────────────────────
    print("\n--- 2. OAuth token fetch + cache ---")
    ft._TOKEN_CACHE["access_token"] = ""
    ft._TOKEN_CACHE["expires_at"] = 0.0
    token_body = json.dumps({"access_token": "ft_token_abc123", "expires_in": 1800, "token_type": "Bearer"}).encode()
    call_count = 0
    def _token_side_effect(req, **kw):
        nonlocal call_count
        call_count += 1
        # Distinguer POST token (method=POST, Content-Type form) vs GET search
        if req.method == "POST":
            return _mock_response(200, token_body)
        # GET search → retourne 0 résultats pour ce test (on teste juste le token ici)
        return _mock_response(200, json.dumps({"resultats": []}).encode())
    with patch.object(settings, "france_travail_client_id", "test_cid"), \
         patch.object(settings, "france_travail_client_secret", "test_csecret"), \
         patch("urllib.request.urlopen", side_effect=_token_side_effect):
        # 1er search → demande token (call 1) + search (call 2)
        ft.search_france_travail(SearchCriteria(keywords="dev"))
        ck(call_count == 2, f"1er search : 2 calls HTTP (token + search) — got {call_count}")
        ck(ft._TOKEN_CACHE["access_token"] == "ft_token_abc123", "token caché après 1er fetch")
        # 2e search → token réutilisé (pas de nouveau POST token) → 1 call (search seulement)
        call_count = 0
        ft.search_france_travail(SearchCriteria(keywords="dev"))
        ck(call_count == 1, f"2e search : 1 call HTTP (token réutilisé, pas de refetch) — got {call_count}")

    # ── 3. search success (mapping complet) ───────────────────────────────
    print("\n--- 3. search success (mapping) ---")
    ft._TOKEN_CACHE["access_token"] = ""
    ft._TOKEN_CACHE["expires_at"] = 0.0
    offre_ft = {
        "id": "ABCDEF123",
        "intitule": "Développeur Python Senior",
        "entreprise": {"nom": "TechCorp SAS"},
        "lieuTravail": {"libelle": "Paris (75)"},
        "origineOffre": {"urlOrigine": "https://candidat.francetravail.fr/offres/ABCDEF123"},
        "description": "<p>Belle opportunité en <b>Python</b>.</p><br>Développement backend.",
        "typeContrat": "CDI",
        "salaire": {"libelle": "45 000 - 55 000 Euros/Année"},
        "dateCreation": 1737000000000,  # epoch ms
    }
    search_body = json.dumps({"resultats": [offre_ft], "contentRange": "0-1/1"}).encode()
    token_body = json.dumps({"access_token": "tok_xyz", "expires_in": 1800}).encode()
    def _search_side_effect(req, **kw):
        if req.method == "POST":
            return _mock_response(200, token_body)
        return _mock_response(200, search_body)
    with patch.object(settings, "france_travail_client_id", "cid"), \
         patch.object(settings, "france_travail_client_secret", "cs"), \
         patch("urllib.request.urlopen", side_effect=_search_side_effect):
        res = ft.search_france_travail(SearchCriteria(keywords="python", location="Paris", contract_types=["CDI"]))
        ck(res.status == "ok", f"status=ok (got {res.status})")
        ck(len(res.postings) == 1, f"1 posting (got {len(res.postings)})")
        ck(res.fetched_count == 1, "fetched_count=1")
        p = res.postings[0]
        ck(p.title == "Développeur Python Senior", f"title mappé (got {p.title})")
        ck(p.company == "TechCorp SAS", f"company mappé (got {p.company})")
        ck(p.location == "Paris (75)", f"location mappé (got {p.location})")
        ck(p.contract_type == "CDI", f"contract_type=CDI (got {p.contract_type})")
        ck(p.salary_min == 45000, f"salary_min=45000 (got {p.salary_min})")
        ck(p.salary_max == 55000, f"salary_max=55000 (got {p.salary_max})")
        ck(p.external_id == "ABCDEF123", f"external_id mappé (got {p.external_id})")
        ck(p.url and "ABCDEF123" in p.url, f"url mappé (got {p.url})")
        ck(p.currency == "EUR", "currency=EUR ( défaut France Travail)")
        ck(p.posted_at is not None, f"posted_at parsé (got {p.posted_at})")
        ck("<p>" not in (p.description or ""), f"description HTML strippé (got {(p.description or '')[:50]})")
        ck("Python" in (p.description or ""), "description garde le texte brut")
        ck(p.raw_data.get("id") == "ABCDEF123", "raw_data conservé (audit/re-parse)")

    # ── 4. search 0 résultats (légitime) ──────────────────────────────────
    print("\n--- 4. search 0 résultats (légitime) ---")
    ft._TOKEN_CACHE["access_token"] = "cached_tok"
    ft._TOKEN_CACHE["expires_at"] = 9999999999.0  # token valide (pas de refetch)
    empty_body = json.dumps({"resultats": []}).encode()
    with patch.object(settings, "france_travail_client_id", "cid"), \
         patch.object(settings, "france_travail_client_secret", "cs"), \
         patch("urllib.request.urlopen", return_value=_mock_response(200, empty_body)):
        res = ft.search_france_travail(SearchCriteria(keywords="rare_keyword_xyz"))
        ck(res.status == "ok", f"status=ok même si 0 résultats (got {res.status})")
        ck(len(res.postings) == 0, "0 postings (légitime, pas un échec)")
        ck(res.fetched_count == 0, "fetched_count=0")

    # ── 5. search 401 unauthorized ────────────────────────────────────────
    print("\n--- 5. search 401 unauthorized ---")
    ft._TOKEN_CACHE["access_token"] = "expired_tok"
    ft._TOKEN_CACHE["expires_at"] = 9999999999.0  # token cru valide mais API dit 401
    http_err_401 = urllib.error.HTTPError(
        url="https://api.francetravail.io/offres/search", code=401,
        msg="Unauthorized", hdrs=None, fp=None)
    with patch.object(settings, "france_travail_client_id", "cid"), \
         patch.object(settings, "france_travail_client_secret", "cs"), \
         patch("urllib.request.urlopen", side_effect=http_err_401):
        res = ft.search_france_travail(SearchCriteria(keywords="dev"))
        ck(res.status == "unauthorized", f"status=unauthorized (got {res.status})")
        ck(len(res.postings) == 0, "0 postings sur 401")
        ck(ft._TOKEN_CACHE["access_token"] == "", "token cache invalidé après 401 (force refetch)")

    # ── 6. search error/timeout (URLError) ───────────────────────────────
    print("\n--- 6. search error/timeout ---")
    ft._TOKEN_CACHE["access_token"] = "cached_tok"
    ft._TOKEN_CACHE["expires_at"] = 9999999999.0
    url_err = urllib.error.URLError("timeout")
    with patch.object(settings, "france_travail_client_id", "cid"), \
         patch.object(settings, "france_travail_client_secret", "cs"), \
         patch("urllib.request.urlopen", side_effect=url_err):
        res = ft.search_france_travail(SearchCriteria(keywords="dev"))
        ck(res.status == "error", f"status=error (got {res.status})")
        ck(len(res.postings) == 0, "0 postings sur error réseau")
        ck("injoignable" in (res.error or ""), f"error mentionne injoignable (got {res.error})")

    # ── 7. Mapping helpers (unit tests) ───────────────────────────────────
    print("\n--- 7. Mapping helpers ---")
    # _strip_html
    s = ft._strip_html("<p>Hello <b>world</b></p><br>End")
    ck("Hello" in s and "<" not in s, f"_strip_html : HTML→texte (got {s})")
    ck(ft._strip_html(None) is None, "_strip_html(None)=None")
    ck(ft._strip_html("") is None, "_strip_html('')=None")
    # _parse_salary
    ck(ft._parse_salary("45 000 - 55 000 Euros/Année") == (45000, 55000), "_parse_salary range 45k-55k")
    ck(ft._parse_salary("40 000 Euros/Année") == (40000, None), "_parse_salary single 40k")
    ck(ft._parse_salary(None) == (None, None), "_parse_salary(None)=(None,None)")
    ck(ft._parse_salary("negotiable") == (None, None), "_parse_salary non-parsable=(None,None)")
    # _parse_date_creation
    dt = ft._parse_date_creation(1737000000000)
    ck(isinstance(dt, datetime), "_parse_date_creation → datetime")
    ck(ft._parse_date_creation(None) is None, "_parse_date_creation(None)=None")
    # _build_params
    params = ft._build_params(SearchCriteria(keywords="python", contract_types=["CDI", "CDD"]))
    ck(params.get("motsCles") == "python", f"_build_params motsCles (got {params.get('motsCles')})")
    ck(params.get("typeContrat") == "CDI", f"_build_params typeContrat CDI (got {params.get('typeContrat')})")
    ck(params.get("range") == "0-149", f"_build_params range 0-149 (got {params.get('range')})")
    # contract mapping freelance → MISC
    params_f = ft._build_params(SearchCriteria(contract_types=["freelance"]))
    ck(params_f.get("typeContrat") == "MISC", f"_build_params freelance→MISC (got {params_f.get('typeContrat')})")
    # _map_offre complète
    offre = {"id": "X1", "intitule": "Dev", "entreprise": {"nom": "Corp"}, "lieuTravail": {"libelle": "Lyon"},
             "typeContrat": "CDD", "dateCreation": 1737000000000, "description": "<p>Desc</p>"}
    jp = ft._map_offre(offre)
    ck(isinstance(jp, JobPostingData), "_map_offre → JobPostingData")
    ck(jp.title == "Dev" and jp.company == "Corp", "_map_offre title+company")
    ck(jp.contract_type == "CDD", f"_map_offre contract CDD (got {jp.contract_type})")
    # _map_offre champs manquants (titre défaut)
    jp2 = ft._map_offre({"id": "X2"})
    ck(jp2.title == "Offre sans titre", "_map_offre titre défaut si manquant")

    # ── 8. Registry integration ──────────────────────────────────────────
    print("\n--- 8. Registry integration ---")
    c = get_connector("france_travail")
    ck(c is not None, "get_connector('france_travail') retourne le connecteur")
    ck(c.name == "france_travail", f"name=france_travail (got {c.name})")
    ck(c.auth_method == "oauth2", f"auth_method=oauth2 v2 (got {c.auth_method})")
    ck(c.base_url == "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search", "base_url API v2")
    # configured reflete credentials env
    with patch.object(settings, "france_travail_client_id", ""), \
         patch.object(settings, "france_travail_client_secret", ""):
        ck(c.configured is False, "configured=False sans credentials (registry wrapper délègue à _is_configured)")
    with patch.object(settings, "france_travail_client_id", "cid"), \
         patch.object(settings, "france_travail_client_secret", "cs"):
        ck(c.configured is True, "configured=True avec credentials")
    # search via registry wrapper délègue à search_france_travail
    with patch.object(settings, "france_travail_client_id", ""), \
         patch.object(settings, "france_travail_client_secret", ""):
        res = c.search(SearchCriteria(keywords="test"))
        ck(res.status == "not_configured", "registry.search() délègue à search_france_travail (not_configured)")
    # list_connectors expose france_travail avec auth_method oauth2
    info = next(c for c in list_connectors() if c["name"] == "france_travail")
    ck(info["auth_method"] == "oauth2", f"list_connectors france_travail auth_method=oauth2 (got {info['auth_method']})")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
