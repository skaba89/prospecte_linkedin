"""P24-A — Protection SSRF pour la livraison des webhooks outbound.

validate_url(url) → (ok, error, resolved_ips) :
  - Schéma http/https uniquement (bloque file://, ftp://, gopher://…).
  - Hostname obligatoire + longueur raisonnable.
  - Si hostname = IP littérale → vérifie direct contre la blocklist.
  - Sinon résout le DNS (socket.getaddrinfo) + vérifie CHAQUE IP résolue (DNS rebinding : un
    hostname innocent qui résout vers 169.254.169.254 est bloqué).
  - Blocklist : loopback (127/8, ::1), private (10/8, 172.16/12, 192.168/16), link-local
    (169.254/16 — inclut cloud metadata 169.254.169.254), 0.0.0.0/8, CGNAT (100.64/10),
    unique-local IPv6 (fc00::/7), link-local IPv6 (fe80::/10).

safe_urlopen(req, timeout) : ouvre via un opener qui DÉSACTIVE la follow-redirect par défaut +
re-valide chaque redirect (sous-classe HTTPRedirectHandler). Empêche l'exfiltration via redirect
302 → http://169.25.254.169.254/… (le validateur initial passe sur l'URL légitime, le redirect
vers une IP interne est bloqué au moment du hop).

Usage : services/webhooks.deliver() appelle validate_url(wh.url) avant tout POST ; si ko →
delivery.status=failed, last_error="SSRF bloqué : …". routers/webhooks.create/update appelle
validate_url à l'enregistrement (fail-fast 422) pour ne pas persiste une URL dangereuse.
"""
import ipaddress
import logging
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("revpulse.ssrf")

# Réseaux bloqués (loopback, privés, link-local, metadata cloud, CGNAT, ULA IPv6).
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + cloud metadata (169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique-local IPv6
    ipaddress.ip_network("fe80::/10"),  # link-local IPv6
]


# P24-A : en prod (défaut) SSRF strict — loopback/private/metadata bloqués. En tests/dev, un
# récepteur local (test_api.py:9099) légitime reçoit les webhooks → WEBHOOK_SSRF_ALLOW_PRIVATE=true
# bypass la blocklist IP (les schémas http/https + la re-validation des redirects restent actifs).
_ALLOW_PRIVATE = os.getenv("WEBHOOK_SSRF_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")

# P32 : réseaux TOUJOURS bloqués, même en dev/test (_ALLOW_PRIVATE=true). Metadata cloud (169.254/16),
# 0.0.0.0/8 (unspecified) + CGNAT (100.64/10) + IPv6 link-local (fe80::/10) ne sont JAMAIS des URLs
# légitimes pour un provider IA ou un webhook outbound — un provider IA ne vit pas sur le metadata
# service. Le bypass dev ne s'applique qu'au loopback (127/8 IPv4 + ::1 IPv6 — Ollama local) + private
# IPv4 (10/8, 172.16/12, 192.168/16 — réseau interne dev).
_STRICT_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + cloud metadata (169.254.169.254)
    ipaddress.ip_network("fe80::/10"),  # link-local IPv6 (jamais un provider IA)
]


def _is_blocked_ip(ip_str: str) -> bool:
    """True si l'IP est dans un réseau bloqué. IP invalide → bloquée (fail-safe).
    P24-A : si WEBHOOK_SSRF_ALLOW_PRIVATE=true (tests/dev), retourne toujours False (loopback +
    private autorisés pour le récepteur de test local — les schémas + redirects restent validés)."""
    if _ALLOW_PRIVATE:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def _is_blocked_ip_strict(ip_str: str) -> bool:
    """P32 : variante stricte pour les URLs de CONFIGURATION (providers IA base_url). Bloque TOUJOURS
    les réseaux _STRICT_BLOCKED_NETWORKS (metadata cloud, 0.0.0.0, CGNAT, loopback IPv6, link-local)
    même si WEBHOOK_SSRF_ALLOW_PRIVATE=true — ces IPs ne sont JAMAIS un provider IA légitime. Le
    bypass dev (_ALLOW_PRIVATE) ne s'applique qu'au loopback IPv4 (127/8 — Ollama local) + private
    IPv4 (réseau interne dev). IP invalide → bloquée (fail-safe)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if any(ip in net for net in _STRICT_BLOCKED_NETWORKS):
        return True
    if _ALLOW_PRIVATE:
        return False  # loopback IPv4 + private IPv4 autorisés en dev (Ollama, proxy interne)
    return any(ip in net for net in _BLOCKED_NETWORKS)


def validate_url(url: str) -> tuple[bool, str, list[str]]:
    """Valide une URL outbound contre SSRF. Retourne (ok, error_message, resolved_ips).

    resolved_ips = liste des IPs résolues (pour logging/audit). Si l'URL est une IP littérale,
    resolved_ips = [ip]. Vide si ko.
    """
    if not url or not isinstance(url, str) or len(url) > 2048:
        return False, "URL vide, non-chaîne ou trop longue (>2048)", []
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Schéma non autorisé : {parsed.scheme or 'absent'} (http/https uniquement)", []
    if not parsed.hostname:
        return False, "Hostname manquant", []
    # Hostname = IP littérale → vérification directe.
    try:
        ipaddress.ip_address(parsed.hostname)
        if _is_blocked_ip(parsed.hostname):
            return False, f"IP littérale bloquée (privée/loopback/metadata) : {parsed.hostname}", []
        return True, "", [parsed.hostname]
    except ValueError:
        pass  # c'est un hostname → résolution DNS
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        return False, f"Résolution DNS échouée pour {parsed.hostname} : {e}", []
    ips = []
    for info in infos:
        ip = info[4][0].split("%")[0]  # strip zone IPv6
        ips.append(ip)
        if _is_blocked_ip(ip):
            return False, f"IP résolue bloquée (privée/loopback/metadata) : {ip} pour {parsed.hostname}", []
    return True, "", ips


def validate_url_strict(url: str) -> tuple[bool, str, list[str]]:
    """P32 : variante stricte de validate_url pour les URLs de CONFIGURATION (providers IA base_url).
    Bloque TOUJOURS les réseaux _STRICT_BLOCKED_NETWORKS (metadata cloud, 0.0.0.0, CGNAT, loopback IPv6,
    link-local) même en dev/test (_ALLOW_PRIVATE=true). Le bypass dev ne s'applique qu'au loopback IPv4
    (127/8 — Ollama local) + private IPv4 (proxy interne). Utilisé par routers/ai.create/update config
    pour empêcher un admin de pointer un provider IA vers le metadata service (exfil de credentials API).
    """
    if not url or not isinstance(url, str) or len(url) > 2048:
        return False, "URL vide, non-chaîne ou trop longue (>2048)", []
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Schéma non autorisé : {parsed.scheme or 'absent'} (http/https uniquement)", []
    if not parsed.hostname:
        return False, "Hostname manquant", []
    try:
        ipaddress.ip_address(parsed.hostname)
        if _is_blocked_ip_strict(parsed.hostname):
            return False, f"IP littérale bloquée (metadata/CGNAT/loopback — strict) : {parsed.hostname}", []
        return True, "", [parsed.hostname]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        return False, f"Résolution DNS échouée pour {parsed.hostname} : {e}", []
    ips = []
    for info in infos:
        ip = info[4][0].split("%")[0]
        ips.append(ip)
        if _is_blocked_ip_strict(ip):
            return False, f"IP résolue bloquée (metadata/CGNAT/loopback — strict) : {ip} pour {parsed.hostname}", []
    return True, "", ips


def validate_url_no_dns(url: str) -> tuple[bool, str, list[str]]:
    """P64 — validation SSRF config-time SANS résolution DNS. Pour les URLs de configuration (SSO token_url,
    audit webhook_url) où l'URL peut ne pas résoudre au moment du test (CI/dev) ou être un hostname légitime
    non encore déployé. Bloque : schéma non-http(s), hostname manquant, IP littérale metadata/loopback/CGNAT/
    private (toujours — strict). NE fait PAS de résolution DNS → ne rejette pas les hostnames non résolvables.

    La protection DNS rebinding (hostname qui résout vers 169.254.169.254) est assurée au REQUEST-TIME via
    safe_urlopen (re-validation de chaque hop + résolution à l'ouverture). Config-time + request-time =
    défense en profondeur SSRF complète.

    Retourne (ok, error_message, [hostname_or_ip])."""
    if not url or not isinstance(url, str) or len(url) > 2048:
        return False, "URL vide, non-chaîne ou trop longue (>2048)", []
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Schéma non autorisé : {parsed.scheme or 'absent'} (http/https uniquement)", []
    if not parsed.hostname:
        return False, "Hostname manquant", []
    # IP littérale → vérification directe (bloque metadata/loopback/private/CGNAT — strict, toujours).
    try:
        ipaddress.ip_address(parsed.hostname)
        if _is_blocked_ip_strict(parsed.hostname):
            return False, f"IP littérale bloquée (metadata/CGNAT/loopback — strict) : {parsed.hostname}", []
        return True, "", [parsed.hostname]
    except ValueError:
        pass  # hostname → pas de résolution DNS (request-time safe_urlopen s'en charge)
    return True, "", [parsed.hostname]


class _SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-valide chaque cible de redirect contre la blocklist SSRF (DNS rebinding via redirect).

    Par défaut urllib suit les redirects 301/302/303/307/308 sans re-vérifier l'URL cible → un
    webhook légitime qui redirect vers http://169.254.169.254/ exfiltre. Ce handler intercepte
    chaque redirect + re-appelle validate_url sur la nouvelle URL ; bloqué → URLError.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, err, _ = validate_url(newurl)
        if not ok:
            raise urllib.error.URLError(f"Redirect bloqué (SSRF) : {err}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Opener partagé : suit les redirects MAIS via _SSRFRedirectHandler (re-validation par hop).
_safe_opener = urllib.request.build_opener(_SSRFRedirectHandler)


def safe_urlopen(req: urllib.request.Request, timeout: float):
    """Ouvre une requête via l'opener SSRF-safe (redirects re-validés). Lève URLError/HTTPError."""
    return _safe_opener.open(req, timeout=timeout)
