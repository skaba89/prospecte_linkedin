/* RevPulse — enhancements P0 : Studio (composer + file enrichie + Refuser + LinkedIn).
 * Module indépendant, chargé en defer après le script principal. Actif seulement en mode servi
 * (présence d'un token API). N'altère pas le rendu existant : ajoute un FAB + un drawer.
 *
 * Dépend (globales exposées par index.html) : apiFetch(path, opts), apiTok(), _SESS.activeTenantId, _USER.
 */
(function () {
  "use strict";
  if (typeof apiFetch !== "function") return; // mode standalone (pas d'API) → on sort

  var STUDIO = null; // nœud drawer
  var FAB = null;

  // ---- helpers ----
  function tid() { return (_SESS && _SESS.activeTenantId) || ""; }
  function api(path, opts) { return apiFetch(path, opts); } // alias court
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  // ---- styles ----
  function injectStyles() {
    var css = [
      "#rp-fab{position:fixed;right:22px;bottom:22px;z-index:9998;background:#635bff;color:#fff;border:none;",
      "border-radius:50%;width:58px;height:58px;font-size:24px;box-shadow:0 6px 20px rgba(99,91,255,.45);",
      "cursor:pointer;display:flex;align-items:center;justify-content:center;transition:transform .15s}",
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
    var s = el("style", null, css);
    document.head.appendChild(s);
  }

  // ---- FAB + drawer skeleton ----
  function buildSkeleton() {
    FAB = el("button", null, "✍️");
    FAB.id = "rp-fab";
    FAB.title = "Studio RevPulse (composer + file + LinkedIn)";
    FAB.onclick = toggle;
    document.body.appendChild(FAB);

    STUDIO = el("div"); STUDIO.id = "rp-studio";
    STUDIO.innerHTML =
      '<h3>✍️ Studio — authoring &amp; LinkedIn</h3>'
      + '<div class="rp-sec" id="rp-lk-sec"></div>'
      + '<div class="rp-sec"><strong>Composer un contenu</strong>'
      + '  <label>Type</label><select id="rp-c-type"><option value="post">Post</option><option value="dm">DM</option><option value="comment">Commentaire</option></select>'
      + '  <label>Titre</label><input id="rp-c-title" placeholder="Titre court du contenu">'
      + '  <label>Priorité</label><select id="rp-c-prio"><option value="high">Haute</option><option value="medium" selected>Normale</option><option value="low">Basse</option></select>'
      + '  <label>Corps (texte du post — publié sur LinkedIn)</label><textarea id="rp-c-body" placeholder="Texte à publier..."></textarea>'
      + '  <div style="margin-top:8px"><button class="rp-btn rp-ok" id="rp-c-submit">Soumettre à la file</button></div>'
      + '</div>'
      + '<div class="rp-sec"><strong>File d\'approbation (<span id="rp-pending-c">0</span>)</strong><div id="rp-pending"></div></div>'
      + '<div class="rp-sec"><strong>Historique (approuvés / refusés)</strong><div id="rp-history"></div></div>';
    document.body.appendChild(STUDIO);
    document.getElementById("rp-c-submit").onclick = submitContent;
  }

  function toggle() {
    if (!STUDIO) return;
    var open = STUDIO.classList.toggle("open");
    if (open) refresh();
  }

  // ---- data fetch + render ----
  async function refresh() {
    if (!tid()) return;
    renderLinkedIn(); // lance son propre fetch
    try {
      var aps = await api("/api/tenants/" + tid() + "/approvals");
      renderPending(aps);
    } catch (e) { document.getElementById("rp-pending").innerHTML = '<div class="rp-empty">Erreur file : ' + esc(e.message || e) + "</div>"; }
    try {
      var hist = await api("/api/tenants/" + tid() + "/approvals/history");
      renderHistory(hist);
    } catch (e) { document.getElementById("rp-history").innerHTML = '<div class="rp-empty">Erreur historique</div>'; }
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

  async function renderLinkedIn() {
    var box = document.getElementById("rp-lk-sec");
    try {
      var st = await api("/api/tenants/" + tid() + "/linkedin/status");
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
    var body = {
      type: document.getElementById("rp-c-type").value,
      title: document.getElementById("rp-c-title").value.trim(),
      priority: document.getElementById("rp-c-prio").value,
      content_body: document.getElementById("rp-c-body").value,
      submitter: (_USER && _USER.name) || "Moi",
    };
    if (!body.title) { banner("Titre requis", true); return; }
    try {
      await api("/api/tenants/" + tid() + "/content", { method: "POST", body: body });
      document.getElementById("rp-c-title").value = "";
      document.getElementById("rp-c-body").value = "";
      banner("Contenu soumis à la file ✓", false);
      refresh();
    } catch (e) { banner("Échec soumission : " + (e.message || e), true); }
  }

  // délégation de clic pour approve/reject/publish
  document.addEventListener("click", async function (ev) {
    var b = ev.target.closest ? ev.target.closest("[data-act]") : null;
    if (!b || !STUDIO || !STUDIO.classList.contains("open")) return;
    var act = b.getAttribute("data-act"), aid = b.getAttribute("data-id");
    var base = "/api/tenants/" + tid() + "/approvals/";
    try {
      if (act === "approve") { await api(base + aid + "/approve", { method: "POST" }); banner("Approuvé ✓", false); }
      else if (act === "reject") { await api(base + aid + "/reject", { method: "POST" }); banner("Refusé", false); }
      else if (act === "publish") {
        var r = await api("/api/tenants/" + tid() + "/linkedin/publish?aid=" + aid, { method: "POST" });
        var span = STUDIO.querySelector('.rp-result[data-id="' + CSS.escape(aid) + '"]');
        if (span) span.textContent = r.published ? "Publié : " + r.post_urn : "Échec : " + (r.detail || "");
        if (r.published) banner("Publié sur LinkedIn ✓ " + r.post_urn, false); else banner("Publication échouée", true);
        return;
      }
      refresh();
    } catch (e) { banner("Action échouée : " + (e.message || e), true); }
  });

  async function connectLinkedIn() {
    try {
      var r = await api("/api/tenants/" + tid() + "/linkedin/auth");
      window.location.href = r.auth_url; // LinkedIn OAuth (ou mock-connect en dev)
    } catch (e) { banner("Auth LinkedIn échouée : " + (e.message || e), true); }
  }
  async function disconnectLinkedIn() {
    try { await api("/api/tenants/" + tid() + "/linkedin/disconnect", { method: "POST" }); banner("LinkedIn déconnecté", false); refresh(); }
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

  // ---- boot : attendre que l'app principale ait un tenant actif ----
  function whenReady(cb) {
    if (apiTok() && tid()) cb();
    else setTimeout(function () { whenReady(cb); }, 250);
  }

  document.addEventListener("DOMContentLoaded", function () {
    injectStyles();
    buildSkeleton();
    handleCallback();
    whenReady(function () {
      FAB.style.display = "flex";
      // re-render quand le tenant actif change
      var prev = tid();
      setInterval(function () { var cur = tid(); if (cur !== prev && STUDIO && STUDIO.classList.contains("open")) { prev = cur; refresh(); } }, 800);
    });
  });

  // exposed pour debug
  window.RevPulseStudio = { open: toggle, refresh: refresh };
})();
