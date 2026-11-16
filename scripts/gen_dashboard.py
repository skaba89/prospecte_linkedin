#!/usr/bin/env python3
"""Génère le dashboard Grafana « RevPulse — Overview » (6 panels).

Sortie : observability/grafana/dashboards/revpulse-overview.json

Consomme les métriques P5 (app/metrics.py) :
  - revpulse_http_requests_total{method,route,status}        (Counter)
  - revpulse_http_request_duration_seconds{method,route}     (Histogram)

Panels :
  1. QPS total (stat)            — sum(rate(revpulse_http_requests_total[5m]))
  2. Taux d'erreur 5xx (stat)    — 5xx / total (percentunit)
  3. Répartition par status (pie)— sum(rate(...)) by (status)
  4. QPS par route (timeseries)  — sum(rate(...)) by (route)
  5. Latence p95 par route (ts)  — histogram_quantile(0.95, ...) by (route)
  6. Top 5 routes par trafic (table) — topk(5, ...)

Le datasource Prometheus (uid `prometheus`) est provisionné via
observability/grafana/provisioning/datasources/prometheus.yml.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "observability/grafana/dashboards/revpulse-overview.json"
DS = {"type": "prometheus", "uid": "prometheus"}


def panel(pid, ptype, title, x, y, w, h, expr, legend, unit=None):
    return {
        "id": pid,
        "type": ptype,
        "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": DS,
        "targets": [{"expr": expr, "legendFormat": legend, "refId": "A"}],
        "fieldConfig": {"defaults": {"unit": unit} if unit else {}, "overrides": []},
        "options": {},
    }


panels = [
    # Row 1 — KPIs
    panel(1, "stat", "QPS (total)", 0, 0, 6, 4,
          "sum(rate(revpulse_http_requests_total[5m]))", legend="QPS", unit="reqps"),
    panel(2, "stat", "Taux d'erreur 5xx", 6, 0, 6, 4,
          'sum(rate(revpulse_http_requests_total{status=~"5.."}[5m])) '
          '/ sum(rate(revpulse_http_requests_total[5m]))',
          legend="5xx ratio", unit="percentunit"),
    panel(3, "piechart", "Répartition par status HTTP", 12, 0, 12, 4,
          'sum(rate(revpulse_http_requests_total[5m])) by (status)', legend="{{status}}"),
    # Row 2 — timeseries
    panel(4, "timeseries", "QPS par route", 0, 4, 12, 8,
          "sum(rate(revpulse_http_requests_total[5m])) by (route)",
          legend="{{route}}", unit="reqps"),
    panel(5, "timeseries", "Latence p95 par route (secondes)", 12, 4, 12, 8,
          "histogram_quantile(0.95, "
          "sum(rate(revpulse_http_request_duration_seconds_bucket[5m])) by (le, route))",
          legend="{{route}}", unit="s"),
    # Row 3 — table
    panel(6, "table", "Top 5 routes par trafic", 0, 12, 24, 6,
          "topk(5, sum(rate(revpulse_http_requests_total[5m])) by (route))",
          legend="{{route}}"),
]

dashboard = {
    "title": "RevPulse — Overview",
    "uid": "revpulse-overview",
    "schemaVersion": 39,
    "version": 1,
    "timezone": "browser",
    "refresh": "15s",
    "editable": True,
    "time": {"from": "now-1h", "to": "now"},
    "panels": panels,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Dashboard écrit : {OUT}")
print(f"  {len(panels)} panels | métriques : revpulse_http_requests_total, "
      f"revpulse_http_request_duration_seconds")
