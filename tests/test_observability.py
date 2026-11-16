#!/usr/bin/env python3
"""test_observability.py — validation statique des configs observabilité (P7).

Vérifie (sans runtime) que :
  - prometheus.yml scrape le backend sur /metrics + référence alerts.yml
  - alerts.yml définit 3 alertes référençant les métriques P5
  - Grafana : datasource + dashboard provisioning valides
  - revpulse-overview.json est un JSON valide avec 6 panels consommant les métriques P5
  - docker-compose.observability.yml provisionne prometheus + grafana avec les bons mounts

Autoporté (stdlib + PyYAML). Exit 0 si tout passe. CI : job `frontend` (check statique).
"""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if str(got) == str(expected):
        print(f"  ✓ {label}")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


def read_yaml(rel):
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def read_text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ─── prometheus.yml ──────────────────────────────────────────────────────────
print("=== prometheus.yml ===")
p = read_yaml("observability/prometheus.yml")
ck(p["global"]["scrape_interval"], "15s", "scrape_interval 15s")
ck("alerts.yml" in p["rule_files"], True, "rule_files inclut alerts.yml")
sc = p["scrape_configs"][0]
ck(sc["job_name"], "revpulse-backend", "job_name revpulse-backend")
ck(sc["metrics_path"], "/metrics", "metrics_path /metrics")
ck("backend:8000" in sc["static_configs"][0]["targets"], True, "target backend:8000")

# ─── alerts.yml ──────────────────────────────────────────────────────────────
print("\n=== alerts.yml ===")
a = read_yaml("observability/alerts.yml")
rules = {r["alert"]: r for r in a["groups"][0]["rules"]}
ck("RevPulseBackendDown" in rules, True, "alert RevPulseBackendDown présent")
ck("RevPulseHighErrorRate" in rules, True, "alert RevPulseHighErrorRate présent")
ck("RevPulseHighLatencyP95" in rules, True, "alert RevPulseHighLatencyP95 présent")
alerts_text = read_text("observability/alerts.yml")
ck("revpulse_http_requests_total" in alerts_text, True, "alerts référencent requests_total")
ck("revpulse_http_request_duration_seconds" in alerts_text, True, "alerts référencent duration_seconds")
ck('status=~"5.."' in alerts_text, True, "HighErrorRate filtre status 5xx")
ck("histogram_quantile" in alerts_text, True, "HighLatencyP95 utilise histogram_quantile")
ck(rules["RevPulseBackendDown"]["labels"]["severity"], "critical", "BackendDown severity critical")
ck(rules["RevPulseHighErrorRate"]["labels"]["severity"], "warning", "HighErrorRate severity warning")

# ─── Grafana datasource provisioning ─────────────────────────────────────────
print("\n=== Grafana datasource ===")
ds = read_yaml("observability/grafana/provisioning/datasources/prometheus.yml")
ck(ds["datasources"][0]["type"], "prometheus", "datasource type prometheus")
ck(ds["datasources"][0]["uid"], "prometheus", "datasource uid prometheus")
ck("prometheus:9090" in ds["datasources"][0]["url"], True, "datasource url prometheus:9090")
ck(ds["datasources"][0]["isDefault"], True, "datasource isDefault")

# ─── Grafana dashboard provisioning ──────────────────────────────────────────
print("\n=== Grafana dashboard provisioning ===")
dp = read_yaml("observability/grafana/provisioning/dashboards/revpulse.yml")
ck(dp["providers"][0]["type"], "file", "provider type file")
ck("dashboards" in dp["providers"][0]["options"]["path"], True,
   "provider path contient 'dashboards'")

# ─── revpulse-overview.json ──────────────────────────────────────────────────
print("\n=== revpulse-overview.json (dashboard) ===")
d = json.loads(read_text("observability/grafana/dashboards/revpulse-overview.json"))
ck(d["title"], "RevPulse — Overview", "dashboard title")
ck(d["uid"], "revpulse-overview", "dashboard uid")
panels = d.get("panels", [])
ck(len(panels) >= 6, True, f">= 6 panels ({len(panels)})")
dash_text = json.dumps(d)
ck("revpulse_http_requests_total" in dash_text, True, "dashboard référence requests_total")
ck("revpulse_http_request_duration_seconds" in dash_text, True, "dashboard référence duration_seconds")
ck("histogram_quantile" in dash_text, True, "dashboard utilise histogram_quantile (p95)")
ck("rate(" in dash_text, True, "dashboard utilise rate()")
# le filtre 5xx contient des " échappées dans le JSON → on vérifie les 2 fragments
ck("status=~" in dash_text and "5.." in dash_text, True, "dashboard filtre 5xx (taux d'erreur)")
# types de panels attendus
ptypes = {pn["type"] for pn in panels}
ck("stat" in ptypes, True, "au moins 1 panel stat (KPI)")
ck("timeseries" in ptypes, True, "au moins 1 panel timeseries")
ck("table" in ptypes, True, "au moins 1 panel table (top routes)")

# ─── docker-compose.observability.yml ────────────────────────────────────────
print("\n=== docker-compose.observability.yml ===")
c = read_yaml("docker-compose.observability.yml")
ck("prometheus" in c["services"], True, "service prometheus présent")
ck("grafana" in c["services"], True, "service grafana présent")
prom_vols = " ".join(c["services"]["prometheus"]["volumes"])
ck("prometheus.yml" in prom_vols, True, "prometheus mount prometheus.yml")
ck("alerts.yml" in prom_vols, True, "prometheus mount alerts.yml")
graf_vols = " ".join(c["services"]["grafana"]["volumes"])
ck("provisioning" in graf_vols, True, "grafana mount provisioning")
ck("dashboards" in graf_vols, True, "grafana mount dashboards")
ck(c["services"]["grafana"]["depends_on"], ["prometheus"], "grafana depends_on prometheus")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
