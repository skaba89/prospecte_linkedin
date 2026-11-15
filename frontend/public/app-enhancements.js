/* RevPulse — Studio P0 (authoring + file enrichie + Refuser + LinkedIn).
 * Module AUTONOME : ne dépend d'aucune variable locale du script principal
 * (apiFetch/apiTok/_SESS sont locales à bootSaaS()). Lit le JWT directement
 * dans localStorage (clé 'revpulse_jwt') et fait ses propres fetch avec
 * Authorization: Bearer. Résout le tenant via /api/tenants (premier tenant,
 * caché dans 'rp_active_tid'). Actif seulement si un token est présent.
 *
 * Chargé en <script defer> après le script principal. N'altère pas le rendu
 * existant : ajoute un FAB ✍️ + un drawer latéral.
 */
(function () {
  "use strict";
  var _TOK_KEY = "revpulse_jwt";
  var _TID_KEY = "rp_active_tid";
  var STUDIO = null, FAB = null, _userName = "Moi";

  // ---- helpers autonomes ----
  function tok() { try { return localStorage.getItem(_TOK_KEY) || ""; } catch (e) { return ""; } }
  function tid() { try { return localStorage.getItem(_TID_KEY) || ""; } catch (e) { return ""; } }
  function setTid(t) { try { localStorage.setItem(_TID_KEY, t); } catch (e) {} }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  async function api(path, opts) {
    opts = opts || {};
    var h = { "Content-Type": "application/json" };
    var t = tok(); if (t) h["Authorization"] = "Bearer " + t;
    var r = await fetch(path, { method: opts.method || "GET", headers: h,
      body: opts.body ? JSON.stringify(opts.body) : null });
    var txt = await r.text();
    var data = txt ? JSON.parse(txt) : {};
    if (!r.ok) { var e = new Error(data.detail || ("HTTP " + r.status)); e.status = r.status; e.data = data; throw e; }
    return data;
  }
  async function ensureTid() {
    var t = tid(); if (t) return t;
    var ts = await api("/api/tenants");
    if (!ts.length) throw new Error("Aucun workspace");
    setTid(ts[0].id);
    return ts[0].id;
  }

  // ---- styles ----
  function injectStyles() {
    var css = [
      "#rp-fab{position:fixed;right:22px;bottom:22px;z-index:9998;background:#635bff;color:#fff;border:none;",
      "border-radius:50%;width:58px;height:58px;font-size:24px;box-shadow:0 6px 20px rgba(99,91,255,.45);",
      "cursor:pointer;display:none;align-items:center;justify-content:center;transition:transform .15s}",
      "#rp-fab:hover{transform:scale(1.08)}",
      "#rp-studio{position:fixed;top:0;right:0;width:440px;max-width:92vw;height:100vh;z-index:9999;",
      "background:#0f172a;color:#e2e8f0;box-shadow:-8px 0 32px rgba(0,0,0,.4);overflow-y:auto;",
      "transform:translateX(100%);transition:transform .25s ease;display:flex;flex-direction:column}",
      "#rp-studio.open{transform:translateX(0)}",
      "#rp-studio h3{margin:0;padding:14px 18px;font-size:15px;background:#1e293b;position:sticky;top:0;z-index:2}",
      "#rp-studio .rp-sec{padding:12px 18px;border-bottom:1px solid #1e293b}",
      "#rp-studio label{font-size:11px;color:#94a3b8;display:block;margin:6px 0 3px}",
      "#rp-studio input,#rp-studio select,#rp-studio textarea{width:100%;background:#1e293b;color:#e2e8f0;",
      "border:1px solid #334155;border-radius:6px;padding:7px 9px;font-size:13px;box-sizing:border-box}",
      "#rp-studio textarea{min-height:70px;resize:vertical}",
      "#rp-studio button.rp-btn{border:none;border-radius:6px;padding:6px 11px;font-size:12px;cursor:pointer;margin:2px 0}",
      "#rp-studio .rp-ok{background:#10b981;color:#fff} #rp-studio .rp-no{background:#ef4444;color:#fff}",
      "#rp-studio .rp-pub{background:#0a66c2;color:#fff} #rp-studio .rp-ghost{background:#334155;color:#e2e8f0}",
      "#rp-studio .rp-row{background:#1e293b;border-radius:8px;padding:10px 12px;margin:7px 0}",
      "#rp-studio .rp-badge{display:inline-block;font-size:10px;padding:2px 7px;border-radius:10px;margin-right:5px}",
      "#rp-studio .rp-body{font-size:12px;color:#cbd5e1;margin:6px 0;white-space:pre-wrap;max-height:80px;overflow:hidden}",
      "#rp-studio .rp-empty{color:#64748b;font-size:12px;font-style:italic;padding:8px 0}",
      "#rp-studio .rp-lk{display:flex;align-items:center;justify-content:space-between;gap:8px}",
      "#rp-studio .rp-lk-name{font-size:13px} #rp-studio .rp-lk-sub{font-size:11px;color:#94a3b8}",
      "#rp-banner{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:10000;padding:11px 18px;",
      "border-radius:8px;font-size:13px;color:#fff;box-shadow:0 4px 16px rgba(0,0,0,.4)}",
      "#rp-banner.ok{background:#10b981} #rp-banner.err{background:#ef4444}"
    ].join("");
    document.head.appendChild(el("style", null, css));
  }

  // ---- skeleton ----
  function buildSkeleton() {
    FAB = el("button", null, "✍️");
    FAB.id = "rp-fab";
    FAB.title = "Studio RevPulse (composer + file + LinkedIn)";
    FAB.onclick = toggle;
    document.body.appendChild(FAB);

    STUDIO = el("div"); STUDIO.id = "rp-studio";
    STUDIO.innerHTML =
      '<h3>✍️ Studio — authoring &amp; LinkedIn <span id="rp-tid" style="font-size:11px;color:#94a3b8;font-weight:400"></span></h3>'
      + '<div class="rp-sec" id="rp-lk-sec"><div class="rp-empty">Chargement…</div></div>'
      + '<div class="rp-sec"><strong>Composer un contenu</strong>'
      + '  <label>Type</label><select id="rp-c-type"><option value="post">Post</option><option value="dm">DM</option><option value="comment">Commentaire</option></select>'
      + '  <label>Titre</label><input id="rp-c-title" placeholder="Titre court du contenu">'
      + '  <label>Priorité</label><select id="rp-c-prio"><option value="high">Haute</option><option value="medium" selected>Normale</option><option value="low">Basse</option></select>'
      + '  <label>Corps (texte du post — publié sur LinkedIn)</label><textarea id="rp-c-body" placeholder="Texte à publier..."></textarea>'
      + '  <div style="margin-top:8px"><button class="rp-btn rp-ok" id="rp-c-submit">Soumettre à la file</button></div>'
      + '</div>'
      + '<div class="rp-sec"><strong>File d\'approbation (<span id="rp-pending-c">0</span>)</strong><div id="rp-pending"></div></div>'
      + '<div class="rp-sec"><strong>Historique (approuvés / refusés)</strong><div id="rp-history"></div></div>'
      + '<div class="rp-sec" id="rp-audit-sec"><strong>Historique des actions <span id="rp-audit-c" style="font-size:11px;color:#94a3b8;font-weight:400"></span></strong><div id="rp-audit"></div></div>';
    document.body.appendChild(STUDIO);
    document.getElementById("rp-c-submit").onclick = submitContent;
  }

  function toggle() {
    if (!STUDIO) return;
    if (STUDIO.classList.toggle("open")) refresh();
  }

  // ---- fetch + render ----
  async function refresh() {
    var T, role;
    try {
      var ts = await api("/api/tenants");
      if (!ts.length) throw new Error("Aucun workspace");
      T = ts[0].id; role = ts[0].role || ""; setTid(T);
    } catch (e) { banner("Workspace introuvable : " + (e.message || e), true); return; }
    var tidEl = document.getElementById("rp-tid"); if (tidEl) tidEl.textContent = "· " + T;
    renderLinkedIn(T);
    try {
      var aps = await api("/api/tenants/" + T + "/approvals");
      renderPending(aps);
    } catch (e) { document.getElementById("rp-pending").innerHTML = '<div class="rp-empty">Erreur file : ' + esc(e.message || e) + "</div>"; }
    try {
      var hist = await api("/api/tenants/" + T + "/approvals/history");
      renderHistory(hist);
    } catch (e) { document.getElementById("rp-history").innerHTML = '<div class="rp-empty">Erreur historique</div>'; }
    var asec = document.getElementById("rp-audit-sec");
    if (asec) asec.style.display = (role === "Owner") ? "" : "none";
    if (role === "Owner") renderAudit(T);
  }

  function badge(text, color) { return '<span class="rp-badge" style="background:' + color + '">' + esc(text) + "</span>"; }

  function renderPending(aps) {
    document.getElementById("rp-pending-c").textContent = aps.length;
    var box = document.getElementById("rp-pending");
    if (!aps.length) { box.innerHTML = '<div class="rp-empty">File vide — composez un contenu ci-dessus.</div>'; return; }
    box.innerHTML = aps.map(function (a) {
      return '<div class="rp-row">'
        + badge(a.type_label, a.type_color) + badge(a.priority_label, a.priority_color) + badge(a.status_label, a.status_color)
        + '<div style="font-size:13px;margin:5px 0">' + esc(a.title) + ' <span style="color:#94a3b8">· ' + esc(a.submitter) + " · " + esc(a.submitted_ago) + "</span></div>"
        + (a.content_body ? '<div class="rp-body">' + esc(a.content_body) + "</div>" : (a.summary ? '<div class="rp-body">' + esc(a.summary) + "</div>" : ""))
        + '<div><button class="rp-btn rp-ok" data-act="approve" data-id="' + esc(a.id) + '">Approuver</button>'
        + ' <button class="rp-btn rp-no" data-act="reject" data-id="' + esc(a.id) + '">Refuser</button></div>'
        + "</div>";
    }).join("");
  }

  function renderHistory(hist) {
    var box = document.getElementById("rp-history");
    if (!hist.length) { box.innerHTML = '<div class="rp-empty">Aucun contenu traité.</div>'; return; }
    box.innerHTML = hist.map(function (a) {
      var pub = a.status === "approved"
        ? ' <button class="rp-btn rp-pub" data-act="publish" data-id="' + esc(a.id) + '">Publier sur LinkedIn</button>' : "";
      return '<div class="rp-row">'
        + badge(a.type_label, a.type_color) + badge(a.status_label, a.status_color)
        + '<div style="font-size:13px;margin:5px 0">' + esc(a.title) + "</div>"
        + '<div style="font-size:11px;color:#94a3b8">' + esc(a.submitter) + " · " + esc(a.submitted_ago) + "</div>"
        + '<div style="margin-top:6px"><span class="rp-result" data-id="' + esc(a.id) + '"></span>' + pub + "</div>"
        + "</div>";
    }).join("");
  }

  function renderAudit(T) {
    var box = document.getElementById("rp-audit");
    var cnt = document.getElementById("rp-audit-c");
    api("/api/tenants/" + T + "/audit?limit=20").then(function (entries) {
      if (cnt) cnt.textContent = "(" + entries.length + " affichés)";
      if (!entries.length) { box.innerHTML = '<div class="rp-empty">Aucune action enregistrée.</div>'; return; }
      box.innerHTML = entries.map(function (e) {
        var when = e.created_at ? new Date(e.created_at + "Z").toLocaleString("fr-FR") : "";
        return '<div class="rp-row" style="padding:7px 10px">'
          + '<div style="font-size:12px"><span style="color:#635bff;font-weight:600">' + esc(e.action) + "</span>"
          + ' <span style="color:#94a3b8">· ' + esc(e.actor_name) + "</span></div>"
          + '<div style="font-size:11px;color:#64748b">' + esc(when) + " · " + esc(e.target_type) + " " + esc(e.target_id) + "</div>"
          + "</div>";
      }).join("");
    }).catch(function (e) {
      if (cnt) cnt.textContent = "";
      box.innerHTML = '<div class="rp-empty">Audit indisponible' + (e.status === 403 ? " (réservé au Owner)" : "") + ".</div>";
    });
  }

  async function renderLinkedIn(T) {
    var box = document.getElementById("rp-lk-sec");
    try {
      var st = await api("/api/tenants/" + T + "/linkedin/status");
      if (st.connected) {
        box.innerHTML = '<div class="rp-lk"><div><div class="rp-lk-name">🔗 ' + esc(st.member_name) + "</div>"
          + '<div class="rp-lk-sub">LinkedIn ' + (st.mock ? "(mock)" : "") + " · " + esc(st.member_id) + "</div></div>"
          + '<button class="rp-btn rp-ghost" id="rp-lk-disc">Déconnecter</button></div>';
        document.getElementById("rp-lk-disc").onclick = disconnectLinkedIn;
      } else {
        box.innerHTML = '<div class="rp-lk"><div><div class="rp-lk-name">LinkedIn non connecté</div>'
          + '<div class="rp-lk-sub">' + (st.mock ? "Mode démo — connexion mockée" : "Connexion réelle requise") + "</div></div>"
          + '<button class="rp-btn rp-pub" id="rp-lk-connect">Connecter LinkedIn</button></div>';
        document.getElementById("rp-lk-connect").onclick = connectLinkedIn;
      }
    } catch (e) { box.innerHTML = '<div class="rp-empty">Statut LinkedIn indisponible.</div>'; }
  }

  // ---- actions ----
  async function submitContent() {
    var T; try { T = await ensureTid(); } catch (e) { banner("Pas de workspace", true); return; }
    var body = {
      type: document.getElementById("rp-c-type").value,
      title: document.getElementById("rp-c-title").value.trim(),
      priority: document.getElementById("rp-c-prio").value,
      content_body: document.getElementById("rp-c-body").value,
      submitter: _userName,
    };
    if (!body.title) { banner("Titre requis", true); return; }
    try {
      await api("/api/tenants/" + T + "/content", { method: "POST", body: body });
      document.getElementById("rp-c-title").value = "";
      document.getElementById("rp-c-body").value = "";
      banner("Contenu soumis à la file ✓", false);
      refresh();
    } catch (e) { banner("Échec soumission : " + (e.message || e), true); }
  }

  document.addEventListener("click", async function (ev) {
    var b = ev.target.closest ? ev.target.closest("[data-act]") : null;
    if (!b || !STUDIO || !STUDIO.classList.contains("open")) return;
    var act = b.getAttribute("data-act"), aid = b.getAttribute("data-id");
    var T; try { T = await ensureTid(); } catch (e) { return; }
    var base = "/api/tenants/" + T + "/approvals/";
    try {
      if (act === "approve") { await api(base + aid + "/approve", { method: "POST" }); banner("Approuvé ✓", false); }
      else if (act === "reject") { await api(base + aid + "/reject", { method: "POST" }); banner("Refusé", false); }
      else if (act === "publish") {
        var r = await api("/api/tenants/" + T + "/linkedin/publish?aid=" + aid, { method: "POST" });
        var span = STUDIO.querySelector('.rp-result[data-id="' + CSS.escape(aid) + '"]');
        if (span) span.textContent = r.published ? "Publié : " + r.post_urn : "Échec : " + (r.detail || "");
        if (r.published) banner("Publié sur LinkedIn ✓ " + r.post_urn, false); else banner("Publication échouée", true);
        return;
      }
      refresh();
    } catch (e) { banner("Action échouée : " + (e.message || e), true); }
  });

  async function connectLinkedIn() {
    var T; try { T = await ensureTid(); } catch (e) { return; }
    try {
      var r = await api("/api/tenants/" + T + "/linkedin/auth");
      window.location.href = r.auth_url; // LinkedIn OAuth (ou mock-connect en dev)
    } catch (e) { banner("Auth LinkedIn échouée : " + (e.message || e), true); }
  }
  async function disconnectLinkedIn() {
    var T; try { T = await ensureTid(); } catch (e) { return; }
    try { await api("/api/tenants/" + T + "/linkedin/disconnect", { method: "POST" }); banner("LinkedIn déconnecté", false); refresh(); }
    catch (e) { banner("Déconnexion échouée", true); }
  }

  // ---- banner / callback OAuth ----
  function banner(msg, isErr) {
    var old = document.getElementById("rp-banner"); if (old) old.remove();
    var b = el("div", isErr ? "err" : "ok", esc(msg)); b.id = "rp-banner";
    document.body.appendChild(b);
    setTimeout(function () { if (b.parentNode) b.remove(); }, 4200);
  }
  function handleCallback() {
    var p = new URLSearchParams(location.search);
    var lk = p.get("linkedin");
    if (!lk) return;
    if (lk === "ok") banner("LinkedIn connecté ✓" + (p.get("mock") ? " (mode démo)" : ""), false);
    else banner("Connexion LinkedIn échouée : " + (p.get("detail") || "erreur"), true);
    history.replaceState({}, "", location.pathname);
  }

  // ---- boot : attendre un token (login) ----
  function whenReady(cb) {
    if (tok()) cb();
    else setTimeout(function () { whenReady(cb); }, 300);
  }

  document.addEventListener("DOMContentLoaded", function () {
    injectStyles();
    buildSkeleton();
    handleCallback();
    whenReady(function () {
      if (FAB) FAB.style.display = "flex";
      api("/api/auth/me").then(function (me) { if (me && me.name) _userName = me.name; }).catch(function () {});
    });
    setInterval(function () { if (FAB && !tok()) FAB.style.display = "none"; }, 1000);
  });

  window.RevPulseStudio = { open: toggle, refresh: refresh };
})();
