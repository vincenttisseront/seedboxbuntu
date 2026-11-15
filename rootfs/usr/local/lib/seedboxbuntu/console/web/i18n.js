'use strict';
// SeedBoxBuntu - console : langues. Le français est la langue d'origine du code ; l'anglais est un dictionnaire
// (lang-en.js : phrase française -> phrase anglaise) appliqué au texte affiché, y compris celui que la console
// fabrique après coup. Tout texte absent du dictionnaire reste en français : jamais de clé brute à l'écran.
// Chargé dans <head> après display.js, par la console et par la page d'invitation.
(() => {
  const SKIP = new Set(['SCRIPT', 'STYLE', 'PRE', 'CODE', 'TEXTAREA', 'KBD', 'SVG', 'svg']);
  const ATTRS = ['aria-label', 'placeholder', 'title', 'alt'];
  const norm = (text) => text.replace(/\s+/g, ' ').trim();
  const english = () => document.documentElement.lang === 'en';

  /** Traduit une phrase : correspondance exacte, puis modèles à trous ; espaces d'origine conservés. */
  function tr(text) {
    if (!english() || typeof text !== 'string') return text;
    const dict = window.I18N_EN || {};
    const key = norm(text);
    if (!key) return text;
    let out = dict[key];
    if (out === undefined) {
      for (const [pattern, make] of (window.I18N_PATTERNS || [])) {
        const m = pattern.exec(key);
        if (m) { out = make(...m.slice(1).map((part) => tr(part))); break; }
      }
    }
    if (out === undefined && key.includes(' · ')) {            // « a · b · c » : chaque morceau est traduit à part
      const parts = key.split(' · ').map((part) => tr(part));
      if (parts.join(' · ') !== key) out = parts.join(' · ');
    }
    if (out === undefined) return text;
    return text.slice(0, text.length - text.trimStart().length) + out + text.slice(text.trimEnd().length);
  }

  function translateNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentNode;
      if (!parent || SKIP.has(parent.nodeName) || (parent.closest && parent.closest('.no-i18n'))) return;
      const out = tr(node.nodeValue);
      if (out !== node.nodeValue) node.nodeValue = out;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || (node.classList && node.classList.contains('no-i18n'))) return;
    for (const name of ATTRS) {
      if (node.hasAttribute(name)) {
        const out = tr(node.getAttribute(name));
        if (out !== node.getAttribute(name)) node.setAttribute(name, out);
      }
    }
    if (SKIP.has(node.nodeName)) return;                          // texte de code, de graphique ou de zone de saisie : jamais traduit
    for (const child of node.childNodes) translateNode(child);
  }

  let observer = null;
  function watch() {
    observer = new MutationObserver((records) => {
      observer.disconnect();
      for (const record of records) {
        if (record.type === 'childList') record.addedNodes.forEach(translateNode);
        else if (record.type === 'characterData') translateNode(record.target);
        else if (record.type === 'attributes') translateNode(record.target);
      }
      observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }

  function start() {
    if (!english()) return;
    translateNode(document.body);
    watch();
    document.title = tr(document.title);
  }

  window.I18n = { tr, translateNode, english };
  window.tr = tr;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
