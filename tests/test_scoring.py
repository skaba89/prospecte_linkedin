#!/usr/bin/env python3
"""test_scoring.py — validation statique du scoring avancé (P17).

Couvre (par lecture des sources + introspection, pas de serveur) :
  - crm.py : _compute_score étendu (3 kwargs engagement : emails_sent, last_contact_at,
    has_active_opportunity) + composantes breakdown engagement/opportunity/decay + formule
    (profil cap 100 + engagement cap 40 - decay cap 30, clamp 0-100). Backward compat :
    sans engagement (défaut) → score = profil seul (P11 préservé).
  - crm.py : _score_contact query engagement depuis DB (Notification type=email sent/dry_run
    count + last created_at, Opportunity stage actif prospecting/qualified/proposal/negotiation).
  - crm.py : endpoint POST /crm/contacts/score-recompute (batch, audit crm.score_recompute_batch,
    retourne ScoreRecomputeResult {recomputed, avg_score}).
  - schemas.py : ScoreRecomputeResult.
  - notifications.py : hook recompute après envoi email (import _score_contact depuis crm,
    recompute le score du contact après Notification email créée).
  - Pas de migration (champ Contact.score existe depuis P11).
  - CI : test_scoring.py dans static checks.
"""
import sys
from pathlib import Path

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


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ─── crm.py : _compute_score étendu (P17) ─────────────────────────────────────
print("=== crm.py : _compute_score étendu (P17) ===")
crm = read("backend/app/routers/crm.py")
ck("emails_sent: int = 0" in crm, True, "kwarg emails_sent (défaut 0 = P11)")
ck("last_contact_at: datetime | None = None" in crm, True, "kwarg last_contact_at (défaut None)")
ck("has_active_opportunity: bool = False" in crm, True, "kwarg has_active_opportunity (défaut False)")
ck('breakdown["engagement"]' in crm, True, "composante engagement dans breakdown")
ck('breakdown["opportunity"]' in crm, True, "composante opportunity dans breakdown")
ck('breakdown["decay"]' in crm, True, "composante decay dans breakdown")
ck("emails_sent * 5, 25" in crm, True, "engagement = +5/email cap 25")
ck("15 if has_active_opportunity" in crm, True, "opportunity bonus +15")
ck("min(breakdown[\"engagement\"] + breakdown[\"opportunity\"], 40)" in crm, True, "engagement cap 40")
ck("(datetime.utcnow() - last_contact_at).days" in crm, True, "decay = jours depuis dernier contact")
ck("min(max(" in crm and ", 30)" in crm, True, "decay cap 30 jours")
ck("max(0, min(profile + engagement - decay, 100))" in crm, True, "total = profil + engagement - decay, clamp 0-100")
ck("profile = min(sum(breakdown.values()), 100)" in crm, True, "profil cap 100 (P11 préservé)")

# ─── crm.py : _score_contact (query engagement DB) ────────────────────────────
print("\n=== crm.py : _score_contact (query engagement DB P17) ===")
ck("db.query(models.Notification)" in crm, True, "query Notification count")
ck('recipient_contact_id=c.id, type="email"' in crm, True, "filtre recipient_contact_id + type=email")
ck('models.Notification.status.in_(["sent", "dry_run"])' in crm, True, "filtre status sent/dry_run (engagement valide)")
ck("order_by(models.Notification.created_at.desc())" in crm, True, "last notification (last_contact_at)")
ck("last_notif.created_at if last_notif else None" in crm, True, "last_contact_at extrait (None si jamais contacté)")
ck("db.query(models.Opportunity)" in crm and ".filter_by(tenant_id=c.tenant_id, contact_id=c.id)" in crm, True, "query Opportunity par contact")
ck('models.Opportunity.stage.in_(["prospecting", "qualified", "proposal", "negotiation"])' in crm, True,
   "opportunité active = stage prospecting/qualified/proposal/negotiation (exclut closed)")
ck("emails_sent=emails_sent, last_contact_at=last_contact_at, has_active_opportunity=has_opp" in crm, True,
   "_score_contact passe l'engagement à _compute_score")
ck("def _engagement_signals(" in crm, True, "_engagement_signals helper extrait (DRY — un seul point de query)")
ck("emails_sent, last_contact_at, has_opp = _engagement_signals(db, c)" in crm, True, "_score_contact délègue à _engagement_signals")
ck("return score, breakdown" in crm, True, "_score_contact retourne (score, breakdown) tuple")
ck("score, breakdown = _score_contact(db, c)" in crm, True, "recompute_score appelle _score_contact (pas _compute_score direct — inclut l'engagement)")

# ─── crm.py : endpoint batch recompute ────────────────────────────────────────
print("\n=== crm.py : endpoint POST /crm/contacts/score-recompute (P17) ===")
ck("/contacts/score-recompute" in crm, True, "endpoint path /contacts/score-recompute")
ck("def recompute_all_scores(" in crm, True, "fonction recompute_all_scores définie")
ck('require_permission("crm.manage")' in crm and "recompute_all_scores" in crm, True, "RBAC crm.manage sur batch recompute")
ck("for c in contacts:" in crm and "_score_contact(db, c)" in crm, True, "boucle recompute sur tous les contacts")
ck("crm.score_recompute_batch" in crm, True, "audit crm.score_recompute_batch")
ck("schemas.ScoreRecomputeResult(recomputed=" in crm, True, "retourne ScoreRecomputeResult")
ck("avg_score=round(avg, 1)" in crm, True, "avg_score calculé (round 1 décimale)")

# ─── schemas.py : ScoreRecomputeResult ────────────────────────────────────────
print("\n=== schemas.py : ScoreRecomputeResult (P17) ===")
sch = read("backend/app/schemas.py")
ck("class ScoreRecomputeResult(BaseModel):" in sch, True, "ScoreRecomputeResult défini")
ck("recomputed: int" in sch, True, "field recomputed: int")
ck("avg_score: float" in sch, True, "field avg_score: float")

# ─── notifications.py : hook recompute après envoi email ──────────────────────
print("\n=== notifications.py : hook recompute score (P17) ===")
notif = read("backend/app/routers/notifications.py")
ck("from .crm import _score_contact" in notif, True, "import _score_contact depuis crm (lazy)")
ck("_score_contact(db, contact)" in notif, True, "appel _score_contact après envoi email")
ck("recompute le score du contact" in notif, True, "commentaire P17 recompute après engagement")

# ─── Pas de migration (Contact.score existe depuis P11) ───────────────────────
print("\n=== Pas de migration P17 (champ score existe) ===")
models_txt = read("backend/app/models.py")
ck("score = Column(Integer, default=0, nullable=False)" in models_txt, True, "champ Contact.score présent (P11)")
migs = list((ROOT / "backend/alembic/versions").glob("*.py"))
ck(any("p17" in f.name.lower() for f in migs), False, "AUCUNE migration P17 (score field existe)")

# ─── CI : test_scoring.py ─────────────────────────────────────────────────────
print("\n=== CI : test_scoring.py ===")
ci = read(".github/workflows/ci.yml")
ck("python tests/test_scoring.py" in ci, True, "test_scoring.py dans static checks CI")

# ─── Introspection runtime (signature + route + backward compat) ──────────────
print("\n=== Introspection runtime ===")
sys.path.insert(0, str(ROOT / "backend"))
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    try:
        from app.routers.crm import _compute_score, recompute_all_scores
        from app import schemas, models
        from app.routers import crm as crm_mod
        import inspect
        sig = inspect.signature(_compute_score)
        for p in ["emails_sent", "last_contact_at", "has_active_opportunity"]:
            ck(p in sig.parameters, True, f"_compute_score paramètre {p}")
        # Backward compat : sans engagement → score = profil seul.
        c = models.Contact(tenant_id="t", first_name="A", last_name="B", email="a@b.com",
                           linkedin_url="https://in/a", title="VP Engineering", phone="+33")
        s, b = _compute_score(c, None)
        ck(s, 80, "profil-only score = 80 (4 critères × 20, backward compat P11)")
        ck(b.get("engagement"), 0, "engagement=0 sans emails (défaut)")
        ck(b.get("decay"), 0, "decay=0 sans last_contact_at (défaut)")
        # Avec engagement : 2 emails + opportunité + last_contact récent → score boosté.
        from datetime import datetime, timedelta
        s2, b2 = _compute_score(c, None, emails_sent=2,
                                last_contact_at=datetime.utcnow() - timedelta(days=5),
                                has_active_opportunity=True)
        ck(b2["engagement"], 10, "engagement = 2 emails × 5 = 10")
        ck(b2["opportunity"], 15, "opportunity bonus = 15")
        ck(b2["decay"], -5, "decay = -5 (5 jours)")
        ck(s2, 100, "score clampé 100 (80 + 25 - 5 = 100)")
        # Decay cap 30 : last_contact 40j → decay -30.
        s3, b3 = _compute_score(c, None, emails_sent=1,
                                last_contact_at=datetime.utcnow() - timedelta(days=40))
        ck(b3["decay"], -30, "decay cap 30 (40 jours → -30)")
        ck(s3, 55, "score = 80 + 5 - 30 = 55 (clamp ≥0)")
        # routes batch
        paths = {(r.path, m) for r in crm_mod.router.routes for m in (r.methods - {"HEAD", "OPTIONS"})}
        ck(("/api/tenants/{tid}/crm/contacts/score-recompute", "POST") in paths, True,
           "route /contacts/score-recompute [POST] enregistrée")
    except Exception as e:
        ck(False, True, f"introspection runtime (erreur: {type(e).__name__}: {e})")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
