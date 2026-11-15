"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const ROLES = { admin: "Administrateur", manager: "Gestionnaire", user: "Utilisateur", observer: "Observateur" };
  const PILL = { "Disponible": "ok", "Approuvée": "info", "En préparation": "info", "Partiellement disponible": "info", "En attente d'approbation": "warn", "Refusée": "err", "Échec": "err" };

  const text = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value;
    return node;
  };

  function quota(kind, item) {
    const box = $("me-quota-" + kind);
    box.classList.toggle("hidden", !item);
    if (!item) return false;
    $("me-quota-" + kind + "-text").textContent = item.used + " sur " + item.limit + " utilisée(s) tous les " + item.days + " jours · il en reste " + item.remaining;
    const bar = $("me-quota-" + kind + "-bar");
    bar.value = Math.min(100, Math.round(100 * item.used / item.limit));
    bar.className = item.remaining === 0 ? "err" : item.remaining === 1 ? "warn" : "";
    return true;
  }

  function limits(data) {
    const lines = [];
    if (data.limits.sessions) lines.push(data.limits.sessions + " lecture(s) simultanée(s) au plus");
    if (data.limits.bitrate_mbps) lines.push("débit limité à " + data.limits.bitrate_mbps + " Mb/s hors du réseau local");
    if (!data.limits.downloads) lines.push("téléchargement des médias non autorisé");
    return lines.length ? "Sur votre compte : " + lines.join(", ") + "." : "Aucune limite particulière sur votre compte.";
  }

  function render(data) {
    const account = $("me-account");
    account.replaceChildren();
    for (const [label, value] of [["Identifiant", data.login], ["Rôle", ROLES[data.role] || data.role]]) {
      account.append(text("dt", "", label), text("dd", "", value));
    }
    const expiry = $("me-expiry");
    expiry.classList.toggle("hidden", data.days_left === null);
    if (data.days_left !== null) {
      expiry.textContent = data.days_left > 0 ? "Ce compte est temporaire : il sera supprimé dans " + data.days_left + " jour(s)." : "Ce compte est temporaire : il sera supprimé dans les prochaines heures.";
    }
    $("me-limits").textContent = limits(data);
    $("me-requests-card").classList.toggle("hidden", !data.requests_available);
    if (data.requests_available) {
      const any = quota("movie", data.quota.movie) | quota("tv", data.quota.tv);
      $("me-no-quota").classList.toggle("hidden", !!any);
      const list = $("me-requests");
      list.replaceChildren();
      for (const request of data.requests) {
        const item = document.createElement("li");
        item.className = "row";
        item.append(text("span", "", request.title), text("span", "muted small", request.kind + " · " + request.date), text("span", "pill " + (PILL[request.status] || "info"), request.status));
        list.append(item);
      }
      $("me-no-requests").classList.toggle("hidden", data.requests.length > 0);
    }
    $("me-loading").classList.add("hidden");
    $("me-content").classList.remove("hidden");
  }

  async function start() {
    try {
      const response = await fetch("/me/api/overview", { cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "Erreur " + response.status);
      render(data);
    } catch (error) {
      $("me-loading").classList.add("hidden");
      $("me-error").classList.remove("hidden");
      $("me-error-text").textContent = error.message;
    }
  }
  start();
})();
