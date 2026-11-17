/* app.js — P31 Business frontend : overlay responsive + helpers partagés.
 * Injecte dynamiquement le hamburger + backdrop (sans modifier le HTML des 9 pages) + wire le toggle.
 * Fournit RP.* helpers (api, toast, esc, formatDate, euros) — mirror career.js pour cohérence.
 * Chargé en fin de <body> sur toutes les pages Business. */
(function () {
  "use strict";
  window.RP = window.RP || {};

  // ─── Injection hamburger + backdrop (mobile sidebar off-canvas) ─────────────
  function injectMobileChrome() {
    var topbar = document.querySelector(".topbar");
    var sidebar = document.querySelector(".sidebar");
    if (!topbar || !sidebar) return;
    if (document.querySelector(".rp-menu-btn")) return;  // déjà injecté (re-run safe)
    var btn = document.createElement("button");
    btn.className = "rp-menu-btn";
    btn.setAttribute("aria-label", "Ouvrir le menu");
    btn.textContent = "☰";
    btn.addEventListener("click", function () { toggleSidebar(true); });
    topbar.insertBefore(btn, topbar.firstChild);
    var backdrop = document.createElement("div");
    backdrop.className = "rp-backdrop";
    backdrop.addEventListener("click", function () { toggleSidebar(false); });
    sidebar.parentNode.insertBefore(backdrop, sidebar.nextSibling);
    // Fermer la sidebar quand un nav-item est cliqué (UX mobile)
    sidebar.querySelectorAll(".nav-item, .nav a, nav a").forEach(function (a) {
      a.addEventListener("click", function () { toggleSidebar(false); });
    });
  }
  function toggleSidebar(open) {
    var sidebar = document.querySelector(".sidebar");
    var backdrop = document.querySelector(".rp-backdrop");
    if (!sidebar || !backdrop) return;
    if (open) { sidebar.classList.add("open"); backdrop.classList.add("open"); }
    else { sidebar.classList.remove("open"); backdrop.classList.remove("open"); }
  }

  // ─── Helpers RP.* (mirror career.js) ───────────────────────────────────────
  RP.esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  RP.formatDate = function (iso) {
    if (!iso) return "—"; var d = new Date(iso);
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" });
  };
  RP.formatDateTime = function (iso) {
    if (!iso) return "—"; var d = new Date(iso);
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric" }) + " " +
      d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  };
  RP.euros = function (n) {
    if (n == null || isNaN(n)) return "—";
    return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(n);
  };
  RP.toast = function (msg, kind) {
    var host = document.querySelector(".rp-toast-host");
    if (!host) { host = document.createElement("div"); host.className = "rp-toast-host"; document.body.appendChild(host); }
    var t = document.createElement("div");
    t.className = "rp-toast " + (kind || "");
    t.textContent = msg;
    host.appendChild(t);
    setTimeout(function () { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(function () { t.remove(); }, 300); }, 3200);
  };
  // Wrapper fetch avec auth + JSON (mirror career.js RP.api) — pour usage futur pages Business
  RP.api = async function (url, opts) {
    opts = opts || {};
    var tok = localStorage.getItem("revpulse_jwt");
    var headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    if (tok) headers["Authorization"] = "Bearer " + tok;
    var r = await fetch(url, { method: opts.method || "GET", headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (r.status === 401) { RP.toast("Session expirée — reconnectez-vous.", "error"); return null; }
    var txt = await r.text();
    var data = txt ? JSON.parse(txt) : null;
    if (!r.ok) { var e = new Error((data && (data.detail || data.message)) || "Erreur " + r.status); throw e; }
    return data;
  };
  RP.toggleSidebar = toggleSidebar;

  // ─── Init ──────────────────────────────────────────────────────────────────
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectMobileChrome);
  } else {
    injectMobileChrome();
  }
})();
