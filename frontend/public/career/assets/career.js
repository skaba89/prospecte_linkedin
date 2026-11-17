/* RevPulse Career — Client partagé (P31). Auth guard + API client + nav + helpers.
 * Token JWT stocké dans localStorage['revpulse_jwt']. Redirige vers /career/login.html si absent.
 * Aucune dépendance externe. Vanilla JS (ES6), compatible navigateurs modernes.
 */
(function () {
  "use strict";
  var TOK_KEY = "revpulse_jwt";
  var USER_KEY = "revpulse_user";

  var RP = window.RP = window.RP || {};

  // ─── Token + session ───
  RP.tok = function () { try { return localStorage.getItem(TOK_KEY) || ""; } catch (e) { return ""; } };
  RP.user = function () { try { return JSON.parse(localStorage.getItem(USER_KEY) || "null"); } catch (e) { return null; } };
  RP.setSession = function (token, user) {
    try { localStorage.setItem(TOK_KEY, token); localStorage.setItem(USER_KEY, JSON.stringify(user)); } catch (e) {}
  };
  RP.clearSession = function () { try { localStorage.removeItem(TOK_KEY); localStorage.removeItem(USER_KEY); } catch (e) {} };
  RP.isLoggedIn = function () { return !!RP.tok(); };

  // ─── API client ───
  RP.api = async function (path, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    var t = RP.tok(); if (t) headers["Authorization"] = "Bearer " + t;
    var fetchOpts = { method: opts.method || "GET", headers: headers };
    if (opts.body !== undefined && opts.body !== null) fetchOpts.body = JSON.stringify(opts.body);
    if (opts.form) { // multipart (file upload)
      delete headers["Content-Type"];
      fetchOpts.body = opts.form;
    }
    var r;
    try { r = await fetch(path, fetchOpts); }
    catch (e) { throw new Error("Réseau indisponible : " + e.message); }
    var txt = await r.text();
    var data = txt ? safeParse(txt) : null;
    if (!r.ok) {
      var msg = (data && (data.detail || data.message)) || ("HTTP " + r.status);
      var err = new Error(msg); err.status = r.status; err.data = data; throw err;
    }
    return data;
  };
  function safeParse(t) { try { return JSON.parse(t); } catch (e) { return t; } }

  // ─── Auth guard (call on each protected page) ───
  RP.requireAuth = function () {
    if (!RP.isLoggedIn()) { window.location.href = "/career/login.html"; return false; }
    return true;
  };

  // ─── Helpers ───
  RP.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  };
  RP.formatDate = function (iso) {
    if (!iso) return "—";
    try { var d = new Date(iso); return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" }); }
    catch (e) { return iso; }
  };
  RP.formatDateTime = function (iso) {
    if (!iso) return "—";
    try { var d = new Date(iso); return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return iso; }
  };
  RP.initials = function (name) {
    if (!name) return "?";
    var p = String(name).trim().split(/\s+/);
    return ((p[0] || "?")[0] + (p[1] || "")[0]).toUpperCase();
  };
  RP.euros = function (n) { if (n == null) return "—"; return Number(n).toLocaleString("fr-FR") + " €"; };

  // ─── Toast ───
  RP.toast = function (msg, type) {
    var wrap = document.querySelector(".rp-toast-wrap");
    if (!wrap) { wrap = document.createElement("div"); wrap.className = "rp-toast-wrap"; document.body.appendChild(wrap); }
    var el = document.createElement("div");
    el.className = "rp-toast" + (type ? " " + type : "");
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(function () { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(function () { el.remove(); }, 300); }, 4000);
  };

  // ─── Status badge helpers (applications pipeline) ───
  RP.statusBadge = function (status) {
    var map = {
      submitted: ["Soumise", "info"], interview: ["Entretien", "warning"],
      offer: ["Offre", "success"], rejected: ["Refusé", "danger"], withdrawn: ["Retirée", "neutral"]
    };
    var m = map[status] || [status, "neutral"];
    return '<span class="rp-badge rp-badge-' + m[1] + '">' + RP.esc(m[0]) + "</span>";
  };
  RP.connectorStatusBadge = function (st) {
    var map = { active: ["Actif", "success"], configured: ["Configuré", "info"], stub: ["Simulation", "warning"], error: ["Erreur", "danger"], disabled: ["Désactivé", "neutral"] };
    var m = map[st] || [st, "neutral"];
    return '<span class="rp-badge rp-badge-' + m[1] + '">' + RP.esc(m[0]) + "</span>";
  };

  // ─── Score rendering (P36 — NOT a probability, an explainable match score) ───
  RP.scoreHtml = function (score) {
    if (score == null) return '<span class="rp-muted">—</span>';
    var pct = Math.round(score);
    var cls = pct >= 70 ? "high" : (pct >= 45 ? "mid" : "");
    return '<span class="rp-score"><span class="rp-score-bar"><span class="rp-score-fill ' + cls + '" style="width:' + pct + '%"></span></span>' + pct + "</span>";
  };

  // ─── Nav (sidebar) ───
  RP.renderNav = function (active) {
    var u = RP.user() || {};
    var items = [
      { href: "/career/dashboard.html", icon: "📊", label: "Tableau de bord", key: "dashboard" },
      { href: "/career/profile.html", icon: "👤", label: "Mon profil", key: "profile" },
      { href: "/career/cvs.html", icon: "📄", label: "Mes CV", key: "cvs" },
      { href: "/career/search.html", icon: "🔍", label: "Recherche d'emplois", key: "search" },
      { href: "/career/matching.html", icon: "🎯", label: "Opportunités recommandées", key: "matching" },
      { href: "/career/applications.html", icon: "📋", label: "Mes candidatures", key: "applications" },
      { href: "/career/notifications.html", icon: "🔔", label: "Notifications", key: "notifications" },
      { href: "/career/interviews.html", icon: "🎤", label: "Coach entretiens", key: "interviews" },
      { href: "/career/analytics.html", icon: "📈", label: "Analytics", key: "analytics" },
      { href: "/career/agent.html", icon: "🤖", label: "Agent autonome", key: "agent" }
    ];
    var html = '<div class="rp-brand"><div class="rp-brand-logo">R</div>' +
      '<div><div class="rp-brand-name">RevPulse</div><div class="rp-brand-sub">Career</div></div></div>';
    html += '<nav class="rp-nav">';
    html += '<div class="rp-nav-section">Espace candidat</div>';
    items.forEach(function (it) {
      html += '<a class="rp-nav-item' + (it.key === active ? " active" : "") + '" href="' + it.href + '">' +
        '<span class="rp-nav-icon">' + it.icon + "</span>" + RP.esc(it.label) + "</a>";
    });
    html += '<div class="rp-nav-section">Business</div>';
    html += '<a class="rp-nav-item" href="/"><span class="rp-nav-icon">💼</span>Espace Business</a>';
    html += "</nav>";
    html += '<div class="rp-sidebar-footer"><div class="rp-user-chip"><div class="rp-avatar">' + RP.esc(RP.initials(u.name)) + "</div>" +
      '<div style="min-width:0"><div class="rp-user-name">' + RP.esc(u.name || "Candidat") + "</div>" +
      '<div class="rp-user-email">' + RP.esc(u.email || "") + "</div></div></div></div>";
    return html;
  };

  // ─── Layout bootstrap : inject sidebar + topbar + backdrop ───
  RP.bootstrap = function (active, title) {
    var sidebar = document.querySelector("[data-rp-sidebar]");
    if (sidebar) sidebar.innerHTML = RP.renderNav(active);
    var topTitle = document.querySelector("[data-rp-topbar-title]");
    if (topTitle && title) topTitle.textContent = title;
    var menuBtn = document.querySelector("[data-rp-menu]");
    var backdrop = document.querySelector(".rp-backdrop");
    if (!backdrop) { backdrop = document.createElement("div"); backdrop.className = "rp-backdrop"; document.body.appendChild(backdrop); }
    if (menuBtn) menuBtn.addEventListener("click", function () {
      sidebar.classList.toggle("open"); backdrop.classList.toggle("open");
    });
    backdrop.addEventListener("click", function () { sidebar.classList.remove("open"); backdrop.classList.remove("open"); });
    // Logout button
    var logoutBtn = document.querySelector("[data-rp-logout]");
    if (logoutBtn) logoutBtn.addEventListener("click", async function () {
      try { await RP.api("/api/auth/logout", { method: "POST" }); } catch (e) { /* token may be invalid */ }
      RP.clearSession(); window.location.href = "/career/login.html";
    });
    // P40 — badge non-lus sur l'item nav Notifications (best-effort, n'interrompt pas le rendu).
    RP.refreshUnreadBadge();
  };

  // ─── P40 — badge notifications non lues sur l'item nav ───
  RP.refreshUnreadBadge = async function () {
    if (!RP.isLoggedIn()) return;
    try {
      var u = await RP.api("/api/career/notifications/unread-count");
      var n = (u && u.unread) || 0;
      var navItem = document.querySelector('.rp-nav-item[href="/career/notifications.html"]');
      if (navItem) {
        var existing = navItem.querySelector(".rp-nav-badge");
        if (existing) existing.remove();
        if (n > 0) {
          var b = document.createElement("span");
          b.className = "rp-nav-badge";
          b.textContent = n > 99 ? "99+" : n;
          navItem.appendChild(b);
        }
      }
    } catch (e) { /* silencieux — le badge est cosmétique */ }
  };

  // ─── Logout helper (used by login page too) ───
  RP.logout = async function () {
    try { await RP.api("/api/auth/logout", { method: "POST" }); } catch (e) {}
    RP.clearSession();
    window.location.href = "/career/login.html";
  };

  // ─── Simple router guard : redirect to dashboard if already logged in (login page) ───
  RP.redirectIfAuthed = function () {
    if (RP.isLoggedIn()) window.location.href = "/career/dashboard.html";
  };
})();
