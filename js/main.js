// Injects header.html and footer.html into #site-header and #site-footer
document.addEventListener("DOMContentLoaded", () => {
  include("header.html", "site-header");
  include("footer.html", "site-footer");
});

function include(file, mountId) {
  const el = document.getElementById(mountId);
  if (!el) return;
  fetch(file)
    .then(r => { if (!r.ok) throw new Error(`Failed: ${file}`); return r.text(); })
    .then(html => { el.innerHTML = html; })
    .catch(err => console.error(err));
}