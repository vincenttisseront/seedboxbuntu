'use strict';
// Réglages d'affichage : chargé dans <head> (avant le premier rendu) par la console ET la page
// d'invitation, pour que le thème soit déjà posé quand la page apparaît. Aucun style en ligne :
// tout passe par des attributs de <html> (data-scheme, data-contrast, data-density, data-text)
// que tokens.css interprète. Les choix sont gardés dans le navigateur ; sans stockage
// (navigation privée, données bloquées) les valeurs par défaut s'appliquent.
(() => {
  const KEY = 'sb.display';
  const DEFAULTS = { theme: 'auto', density: 'comfortable', contrast: 'normal', text: 'normal' };
  const CHOICES = {
    theme: ['auto', 'light', 'dark'],
    density: ['comfortable', 'compact'],
    contrast: ['normal', 'high'],
    text: ['normal', 'large', 'xlarge'],
  };
  const THEME_COLOR = { dark: '#020617', light: '#f1f5f9' };
  const dark = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function read() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || '{}');
      const out = { ...DEFAULTS };
      for (const key of Object.keys(DEFAULTS)) if (CHOICES[key].includes(saved[key])) out[key] = saved[key];
      return out;
    } catch (error) { return { ...DEFAULTS }; }
  }

  function apply(settings) {
    const root = document.documentElement;
    const scheme = settings.theme === 'auto' ? (dark && !dark.matches ? 'light' : 'dark') : settings.theme;
    root.dataset.scheme = scheme;
    root.dataset.contrast = settings.contrast;
    root.dataset.density = settings.density;
    root.dataset.text = settings.text;
    const meta = document.querySelector('meta[name=theme-color]');
    if (meta) meta.setAttribute('content', THEME_COLOR[scheme]);
  }

  function save(settings) {
    try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch (error) { /* stockage indisponible : réglage valable pour cette page seulement */ }
  }

  let current = read();
  apply(current);
  if (dark && dark.addEventListener) dark.addEventListener('change', () => apply(current));

  window.Display = {
    CHOICES, DEFAULTS,
    get: () => ({ ...current }),
    set(patch) { current = { ...current, ...patch }; apply(current); save(current); return { ...current }; },
    reset() { current = { ...DEFAULTS }; apply(current); save(current); return { ...current }; },
  };
})();
