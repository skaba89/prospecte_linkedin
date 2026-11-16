"""Client API LinkedIn — fetch des connexions 1er degré pour sync vers CRM (P12).

Mode mock (dev/CI/tests sans App LinkedIn approuvée) : données déterministes réalistes
(8 connexions à titres senior — nourrit le CRM + le segment P11 "Décideurs tech" search=cto).

Mode réel : appelle l'API LinkedIn /v2/connections avec retry/backoff exponentiel
(429 Retry-After + 5xx + erreurs réseau). Best-effort — l'API connections listant les
1er degré est restreinte (requiert App approuvée + scope r_connections). La voie CI est
le mock ; le mode réel s'active en production avec des creds LinkedIn renseignées.

Chaque connexion normalisée : {first_name, last_name, title, headline, linkedin_url, email}.
L'email n'est PAS exposé par l'API connections (scope séparé r_emailaddress) — vide par défaut.
"""
import json
import logging
import time
import urllib.error
import urllib.request

_log = logging.getLogger("revpulse.linkedin.client")

_CONNECTIONS_URL = "https://api.linkedin.com/v2/connections?start=0&count=500"

# Mock déterministe (8 connexions variées — 3 CTO nourrissent le segment P11 "Décideurs tech").
_MOCK_CONNECTIONS = [
    {"first_name": "Sophie", "last_name": "Lambert", "title": "CTO",
     "headline": "CTO at TechCorp",
     "linkedin_url": "https://linkedin.com/in/sophielambert", "email": ""},
    {"first_name": "Marc", "last_name": "Dubois", "title": "VP Engineering",
     "headline": "VP Engineering at FinScale",
     "linkedin_url": "https://linkedin.com/in/marcdubois", "email": ""},
    {"first_name": "Léa", "last_name": "Chen", "title": "Head of Data",
     "headline": "Head of Data at DataPulse",
     "linkedin_url": "https://linkedin.com/in/leachen", "email": ""},
    {"first_name": "Thomas", "last_name": "Rousseau", "title": "CEO",
     "headline": "Founder & CEO at StartupX",
     "linkedin_url": "https://linkedin.com/in/thomasrousseau", "email": ""},
    {"first_name": "Emma", "last_name": "Garcia", "title": "CTO",
     "headline": "CTO at CloudNative",
     "linkedin_url": "https://linkedin.com/in/emmagarcia", "email": ""},
    {"first_name": "Lucas", "last_name": "Moreau", "title": "Engineering Manager",
     "headline": "Engineering Manager at BigTech",
     "linkedin_url": "https://linkedin.com/in/lucasmoreau", "email": ""},
    {"first_name": "Chloé", "last_name": "Bernard", "title": "VP Sales",
     "headline": "VP Sales at GrowthCo",
     "linkedin_url": "https://linkedin.com/in/chloebernard", "email": ""},
    {"first_name": "Hugo", "last_name": "Petit", "title": "CTO",
     "headline": "CTO at DeepLearning Inc",
     "linkedin_url": "https://linkedin.com/in/hugopetit", "email": ""},
]


def fetch_connections(access_token: str, *, mock: bool) -> list[dict]:
    """Récupère les connexions 1er degré. Mock = données déterministes (dev/CI/tests).
    Réel : appelle l'API LinkedIn connections (best-effort, App approuvée + r_connections).

    Retourne une liste d'entrées normalisées {first_name, last_name, title, headline,
    linkedin_url, email}. L'email est vide (l'API connections ne l'expose pas).
    """
    if mock:
        return list(_MOCK_CONNECTIONS)
    data = _http_get_with_retry(_CONNECTIONS_URL, {"Authorization": f"Bearer {access_token}"})
    out: list[dict] = []
    for el in data.get("elements", []):
        person = el.get("element") or el.get("person") or {}
        name = person.get("name") or {}
        out.append({
            "first_name": name.get("givenName") or person.get("firstName", ""),
            "last_name": name.get("familyName") or person.get("lastName", ""),
            "title": person.get("occupation") or person.get("headline", ""),
            "headline": person.get("headline") or person.get("occupation", ""),
            "linkedin_url": person.get("publicProfileUrl")
                             or f"https://linkedin.com/in/{person.get('id', '')}",
            "email": "",  # scope r_emailaddress séparé — non exposé par /v2/connections
        })
    return out


def _http_get_with_retry(url: str, headers: dict, *, max_retries: int = 3,
                         timeout: int = 15) -> dict:
    """GET avec retry/backoff exponentiel sur 429 (Retry-After) + 5xx + erreur réseau.
    Lève après max_retries épuisés. Pas de retry sur 4xx (sauf 429) — erreurs client définitives."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last_exc = e
            # Retry seulement sur 429 (rate-limit) ou 5xx (serveur transitoire).
            if e.code == 429 or e.code >= 500:
                if attempt + 1 < max_retries:
                    retry_after = int(e.headers.get("Retry-After", str(2 ** attempt)))
                    wait = min(max(retry_after, 1), 10)
                    _log.warning("LinkedIn API HTTP %s (tentative %d/%d) — retry dans %ds",
                                 e.code, attempt + 1, max_retries, wait)
                    time.sleep(wait)
                    continue
            raise
        except urllib.error.URLError as e:
            last_exc = e
            if attempt + 1 < max_retries:
                wait = 2 ** attempt
                _log.warning("LinkedIn API erreur réseau (tentative %d/%d) — retry dans %ds",
                             attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue
            raise
    # Épuisé (ne devrait pas être atteint car les branches ci-dessus lèvent ou continuent).
    raise last_exc if last_exc else RuntimeError("LinkedIn API : retry épuisé sans exception.")
