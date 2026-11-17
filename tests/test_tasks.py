#!/usr/bin/env python3
"""test_tasks.py — validation statique + runtime de P23 Tasks & reminders avancés.

Couvre (par lecture des sources + introspection runtime sur sqlite in-memory) :
  - models.py : Task étendu (reminder_at DateTime indexé + reminder_sent Boolean + completed_at).
  - schemas.py : TaskCreate/TaskUpdate reminder_at + TaskOut reminder_at/reminder_sent/completed_at
    + MemberOut.user_id (exposé pour le dropdown assignee).
  - routers/crm.py : create_task passe reminder_at ; update_task auto-set completed_at sur
    status→done + reset reminder_sent sur changement reminder_at ; list_tasks filtres
    overdue/due_today/priority.
  - services/task_reminders.py : process_due_task_reminders (query reminder_at <= now AND NOT
    reminder_sent AND status != done → Notification in-app feed vers assignee + flip
    reminder_sent). Skip si pas d'assignee. Idempotent.
  - services/sequences.py : start_scheduler appelle process_due_task_reminders dans le même loop.
  - migration 0011 : ALTER TABLE tasks ADD 3 colonnes + index reminder_at.
  - frontend tasks.html (filtres + create + badges reminder/overdue + cycle status).
  - CI : test_tasks.py dans static checks.
  - Runtime : create task reminder_at passé + assignee → run scheduler → Notification créée +
    reminder_sent=True → 2e run idempotent (sent=0) → update status→done → completed_at set →
    filtre overdue isole la tâche passée → skip si pas d'assignee.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

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


print("=== test_tasks.py — Tasks & reminders avancés (P23) ===\n")

# ─── Source : models.py ────────────────────────────────────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")

print("=== models.py : Task étendu (reminder_at + reminder_sent + completed_at) ===")
ck('reminder_at = Column(DateTime, nullable=True, index=True)' in models_src, True, "Task.reminder_at (DateTime, indexé)")
ck('reminder_sent = Column(Boolean, default=False, nullable=False)' in models_src, True, "Task.reminder_sent (Boolean default False)")
ck('completed_at = Column(DateTime, nullable=True)' in models_src, True, "Task.completed_at (DateTime nullable)")

# ─── Source : schemas.py ───────────────────────────────────────────────────────
schemas_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")

print("\n=== schemas.py : Task* + MemberOut.user_id ===")
ck("reminder_at: datetime | None = None  # P23 : instant du rappel" in schemas_src, True, "TaskCreate.reminder_at")
ck("reminder_at: datetime | None = None  # P23 : update du rappel" in schemas_src, True, "TaskUpdate.reminder_at")
ck("reminder_at: datetime | None = None  # P23" in schemas_src, True, "TaskOut.reminder_at")
ck("reminder_sent: bool = False  # P23" in schemas_src, True, "TaskOut.reminder_sent")
ck("completed_at: datetime | None = None  # P23" in schemas_src, True, "TaskOut.completed_at")
ck("user_id: str | None = None  # P23" in schemas_src, True, "MemberOut.user_id (pour dropdown assignee)")

# ─── Source : routers/crm.py (endpoints tasks) ─────────────────────────────────
crm_rtr = (ROOT / "backend" / "app" / "routers" / "crm.py").read_text(encoding="utf-8")

print("\n=== routers/crm.py : create/update/list tasks ===")
ck("reminder_at=body.reminder_at" in crm_rtr, True, "create_task passe reminder_at au constructeur")
ck('data["reminder_sent"] = False' in crm_rtr, True, "update_task reset reminder_sent sur changement reminder_at")
ck('data["completed_at"] = datetime.utcnow()' in crm_rtr, True, "update_task auto-set completed_at sur status→done")
ck('data["completed_at"] = None' in crm_rtr, True, "update_task clear completed_at sur retour arrière (done→autre)")
ck('overdue: bool | None = Query' in crm_rtr, True, "list_tasks param overdue")
ck('due_today: bool | None = Query' in crm_rtr, True, "list_tasks param due_today")
ck('priority: str | None = Query' in crm_rtr, True, "list_tasks param priority")
ck("models.Task.due_date < today" in crm_rtr, True, "filtre overdue (due_date < today AND status != done)")
ck("models.Task.due_date == today" in crm_rtr, True, "filtre due_today (due_date == today AND status != done)")
ck("models.Task.priority == priority" in crm_rtr, True, "filtre priority")
ck("@router.delete(\"/tasks/{tkid}\"" in crm_rtr, True, "DELETE /tasks/{id} existe")

# ─── Source : services/task_reminders.py ───────────────────────────────────────
tr_src = (ROOT / "backend" / "app" / "services" / "task_reminders.py").read_text(encoding="utf-8")
print("\n=== services/task_reminders.py : process_due_task_reminders ===")
ck("def process_due_task_reminders" in tr_src, True, "process_due_task_reminders défini")
ck("def run_once" in tr_src, True, "run_once (point d'entrée manuel/test)")
ck("models.Task.reminder_at.isnot(None)" in tr_src, True, "query reminder_at IS NOT NULL")
ck("models.Task.reminder_at <= now" in tr_src, True, "query reminder_at <= now")
ck("~models.Task.reminder_sent" in tr_src, True, "query NOT reminder_sent (idempotence)")
ck('models.Task.status != "done"' in tr_src, True, "query status != done (pas de rappel sur tâche done)")
ck("models.Notification(" in tr_src, True, "crée une Notification")
ck('type="in_app"' in tr_src, True, "Notification type=in_app")
ck('channel="feed"' in tr_src, True, "Notification channel=feed (cloche dashboard)")
ck("user_id=t.assignee_id" in tr_src, True, "Notification.user_id = assignee (destinataire feed)")
ck("t.reminder_sent = True" in tr_src, True, "flip reminder_sent=True après envoi")
ck('action="task.reminder_sent"' in tr_src, True, "audit task.reminder_sent")
ck("if not t.assignee_id:" in tr_src, True, "skip si pas d'assignee")

# ─── Source : services/sequences.py (hook scheduler) ───────────────────────────
seq_src = (ROOT / "backend" / "app" / "services" / "sequences.py").read_text(encoding="utf-8")
print("\n=== services/sequences.py : hook process_due_task_reminders dans start_scheduler ===")
ck("from .task_reminders import process_due_task_reminders" in seq_src, True, "import process_due_task_reminders")
ck("process_due_task_reminders(db)" in seq_src, True, "appelé dans le loop _loop (après process_due_enrollments)")

# ─── Migration 0011 ────────────────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0011_p23_tasks.py").read_text(encoding="utf-8")
print("\n=== migration 0011_p23_tasks ===")
ck('revision = "0011_p23_tasks"' in mig, True, "revision 0011_p23_tasks")
ck('down_revision = "0010_p22_tags"' in mig, True, "down_revision 0010 (chaîne)")
ck(mig.count("op.add_column"), 3, "3 add_column (reminder_at, reminder_sent, completed_at)")
ck('op.add_column("tasks"' in mig, True, "ALTER TABLE tasks ADD COLUMN")
ck('server_default=sa.text("false")' in mig, True, "reminder_sent server_default false")
ck('op.create_index("ix_tasks_reminder_at"' in mig, True, "index ix_tasks_reminder_at")
ck("op.drop_column" in mig, True, "downgrade drop_column")

# ─── Frontend ──────────────────────────────────────────────────────────────────
tasks_html = (ROOT / "frontend" / "public" / "tasks.html").read_text(encoding="utf-8")
print("\n=== frontend tasks.html ===")
ck(bool(tasks_html), True, "tasks.html existe")
ck("loadTasks" in tasks_html, True, "tasks.html loadTasks")
ck("createTask" in tasks_html, True, "tasks.html createTask")
ck("f-overdue" in tasks_html, True, "tasks.html filtre overdue")
ck("f-today" in tasks_html, True, "tasks.html filtre due_today")
ck("f-priority" in tasks_html, True, "tasks.html filtre priority")
ck("new-reminder" in tasks_html, True, "tasks.html input reminder_at (datetime-local)")
ck("reminder_at" in tasks_html, True, "tasks.html passe reminder_at à l'API")
ck("cycleStatus" in tasks_html, True, "tasks.html cycleStatus (todo→in_progress→done)")
ck("/crm/tasks" in tasks_html, True, "tasks.html appelle /crm/tasks")
ck("/members" in tasks_html, True, "tasks.html fetch members (dropdown assignee via user_id)")

# ─── Introspection runtime (sqlite in-memory) ─────────────────────────────────
print("\n=== Introspection runtime : reminder scheduler + auto completed_at + filtres ===")
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))
    from app import database as db_mod
    from app import models as M, schemas as S
    from app.routers import crm as crm_r
    from app.services import task_reminders as tr

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    # seed : tenant + user + contact
    s.add(M.User(id="usr_a", email="a@test.com", name="Alice", password_hash="x"))
    s.add(M.Tenant(id="tnt_a", name="A", plan="Starter", owner_id="usr_a"))
    s.flush()
    c1 = M.Contact(tenant_id="tnt_a", first_name="C1", last_name="X", email="c1@test.com", status="new")
    s.add(c1); s.flush()
    mem = SimpleNamespace(user_id="usr_a", name="Alice", role="owner", tenant_id="tnt_a", status="active")

    # create_task avec reminder_at dans le passé + assignee → éligible au scheduler
    past = datetime.utcnow() - timedelta(hours=1)
    t1 = crm_r.create_task(
        "tnt_a",
        S.TaskCreate(title="Call découverte", contact_id=c1.id, assignee_id="usr_a",
                     due_date=(datetime.utcnow().date() + timedelta(days=1)), priority="high",
                     reminder_at=past),
        db=s, _t=None, membership=mem,
    )
    ck(t1.reminder_at, past, "create_task reminder_at stocké")
    ck(t1.reminder_sent, False, "create_task reminder_sent=False (pas encore envoyé)")
    ck(t1.completed_at, None, "create_task completed_at=None (status=todo)")

    # task sans assignee → scheduler skip
    t2 = crm_r.create_task(
        "tnt_a",
        S.TaskCreate(title="Relance sans assignee", reminder_at=past),
        db=s, _t=None, membership=mem,
    )

    # Run scheduler : 1 task due (t1) avec assignee → 1 notification + reminder_sent=True ; t2 skip
    res = tr.process_due_task_reminders(s)
    ck(res["sent"], 1, "scheduler: 1 reminder envoyé (t1 a un assignee)")
    ck(res["skipped_no_assignee"], 1, "scheduler: 1 skip (t2 sans assignee)")
    s.refresh(t1)
    ck(t1.reminder_sent, True, "t1.reminder_sent=True après scheduler")
    notif_count = s.query(M.Notification).filter_by(user_id="usr_a", type="in_app").count()
    ck(notif_count, 1, "1 Notification in-app créée pour usr_a (assignee de t1)")
    notif = s.query(M.Notification).filter_by(user_id="usr_a", type="in_app").first()
    ck("Call découverte" in notif.subject, True, "Notification.subject contient le titre de la tâche")

    # Idempotence : 2e run → sent=0 (reminder_sent=True pour t1, t2 toujours sans assignee)
    res2 = tr.process_due_task_reminders(s)
    ck(res2["sent"], 0, "scheduler idempotent : 2e run → 0 envoyé (reminder_sent=True)")
    notif_count2 = s.query(M.Notification).filter_by(user_id="usr_a", type="in_app").count()
    ck(notif_count2, 1, "toujours 1 notification (pas de doublon)")

    # update_task status→done → auto-set completed_at
    t1_done = crm_r.update_task("tnt_a", t1.id, S.TaskUpdate(status="done"), db=s, _t=None, membership=mem)
    ck(t1_done.status, "done", "update status=done")
    ck(t1_done.completed_at is not None, True, "auto-set completed_at sur status→done")

    # update_task reminder_at changé → reset reminder_sent=False
    t1_done.reminder_sent = True  # simulate déjà envoyé
    s.flush()
    future = datetime.utcnow() + timedelta(hours=2)
    t1_reset = crm_r.update_task("tnt_a", t1.id, S.TaskUpdate(reminder_at=future), db=s, _t=None, membership=mem)
    ck(t1_reset.reminder_sent, False, "update reminder_at → reset reminder_sent=False (nouveau rappel à partir)")

    # update_task retour arrière (done→todo) → clear completed_at
    t1_back = crm_r.update_task("tnt_a", t1.id, S.TaskUpdate(status="todo"), db=s, _t=None, membership=mem)
    ck(t1_back.completed_at, None, "retour arrière done→todo → clear completed_at")

    # Filtre overdue : tâche avec due_date passé + status != done
    t3 = crm_r.create_task(
        "tnt_a",
        S.TaskCreate(title="Tâche en retard", due_date=(datetime.utcnow().date() - timedelta(days=2))),
        db=s, _t=None, membership=mem,
    )
    fake_resp = SimpleNamespace(headers={})
    overdue_tasks = crm_r.list_tasks(
        "tnt_a", response=fake_resp, page=SimpleNamespace(skip=0, limit=50),
        status=None, contact_id=None, opportunity_id=None, assignee_id=None,
        priority=None, overdue=True, due_today=None, db=s, _t=None,
    )
    overdue_ids = [t.id for t in overdue_tasks]
    ck(t3.id in overdue_ids, True, "filtre overdue=True isole t3 (due_date passé, status=todo)")
    ck(t1.id not in overdue_ids, True, "filtre overdue n'inclut pas t1 (status=todo mais due_date futur)")

    # Filtre priority
    high_tasks = crm_r.list_tasks(
        "tnt_a", response=SimpleNamespace(headers={}), page=SimpleNamespace(skip=0, limit=50),
        status=None, contact_id=None, opportunity_id=None, assignee_id=None,
        priority="high", overdue=None, due_today=None, db=s, _t=None,
    )
    ck(all(t.priority == "high" for t in high_tasks), True, "filtre priority=high → tous high")
    ck(any(t.id == t1.id for t in high_tasks), True, "t1 (priority=high) dans le filtre")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
