// js/include.js
(function () {
  // Pages set window.SITE_ROOT = "" (index) or "../" (subpages) before this runs.
  const ROOT = (typeof window.SITE_ROOT === 'string') ? window.SITE_ROOT : "";

  function loadHTML(id, file, done) {
    fetch(ROOT + file)
      .then(r => { if (!r.ok) throw new Error(`Failed to fetch ${file}`); return r.text(); })
      .then(html => {
        const host = document.getElementById(id);
        if (!host) return;
        host.innerHTML = html;

        // Rewrite header links that have data-hash to root index anchors
        if (file.includes('header.html')) {
          const anchors = host.querySelectorAll('a[data-hash]');
          anchors.forEach(a => {
            const hash = a.getAttribute('data-hash') || '';
            a.setAttribute('href', ROOT + 'index.html' + hash);
          });
        }
        if (typeof done === 'function') done();
      })
      .catch(err => console.error(err));
  }

  // Expose for inline calls
  window.loadHTML = loadHTML;
})();

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-include]").forEach(async el => {
    const file = el.getAttribute("data-include");
    if (file) {
      const resp = await fetch(file);
      if (resp.ok) {
        el.innerHTML = await resp.text();
      }
    }
  });
});