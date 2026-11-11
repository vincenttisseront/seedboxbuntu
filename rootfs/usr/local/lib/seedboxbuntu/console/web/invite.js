"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const token = location.pathname.split("/").filter(Boolean)[1] || "";
  const sections = ["invite-loading", "invite-invalid", "invite-form-card", "invite-progress", "invite-done", "invite-failed"];
  const show = (id) => sections.forEach((s) => $(s).classList.toggle("hidden", s !== id));
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function call(path, body) {
    const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const response = await fetch(path, { ...options, cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Erreur " + response.status);
    return data;
  }

  function perks(profile) {
    const lines = [];
    if (profile.account_days) lines.push("compte valable " + profile.account_days + " jours");
    if (profile.max_sessions) lines.push(profile.max_sessions + " lecture(s) simultanée(s) au plus");
    if (profile.bitrate_mbps) lines.push("débit limité à " + profile.bitrate_mbps + " Mb/s hors du réseau local");
    if (profile.seerr_movie_limit) lines.push(profile.seerr_movie_limit + " demande(s) de film tous les " + profile.seerr_movie_days + " jours");
    if (profile.seerr_tv_limit) lines.push(profile.seerr_tv_limit + " demande(s) de série tous les " + profile.seerr_tv_days + " jours");
    return lines.length ? "Votre compte : " + lines.join(", ") + "." : "";
  }

  async function start() {
    try {
      const info = await call("/invite/api/info?token=" + encodeURIComponent(token));
      if (!info.valid) return show("invite-invalid");
      $("invite-title").textContent = info.label ? "Invitation : " + info.label : "Vous êtes invité";
      $("invite-perks").textContent = perks(info.profile);
      $("done-expiry").textContent = info.profile.account_days ? "Ce compte est temporaire : il sera supprimé dans " + info.profile.account_days + " jours." : "";
      show("invite-form-card");
    } catch (error) {
      show("invite-invalid");
      $("invite-invalid").querySelector("p.muted").textContent = error.message;
    }
  }

  function fail(message) {
    const box = $("invite-error");
    box.textContent = message;
    box.classList.remove("hidden");
    $("invite-submit").disabled = false;
  }

  $("invite-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("invite-error").classList.add("hidden");
    const login = $("invite-login").value.trim().toLowerCase();
    const password = $("invite-password").value;
    if (!/^[a-z][a-z0-9_.-]{1,30}$/.test(login)) return fail("Identifiant invalide : lettres minuscules, chiffres, « . », « _ » ou « - », commençant par une lettre.");
    if (password.length < 8) return fail("Mot de passe trop court : 8 caractères au minimum.");
    if (password !== $("invite-confirm").value) return fail("Les deux mots de passe sont différents.");
    $("invite-submit").disabled = true;
    try {
      const { job } = await call("/invite/api/accept", { token, login, password });
      show("invite-progress");
      for (let i = 0; i < 120; i++) {
        await wait(2000);
        const status = await call("/invite/api/job?id=" + encodeURIComponent(job));
        if (status.state === "ok") { $("done-login").textContent = login; return show("invite-done"); }
        if (status.state === "error") return show("invite-failed");
      }
      show("invite-failed");
    } catch (error) {
      show("invite-form-card");
      fail(error.message);
    }
  });

  start();
})();
