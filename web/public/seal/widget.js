/* Selo "Monitorado por Klarim" (KL-44 P5) — badge factual, sem tracking.
   Uso:
     <div id="klarim-seal"></div>
     <script src="https://klarim.net/seal/widget.js" data-domain="site.com.br"></script>
   NÃO envia dados do visitante ao Klarim; só faz 1 GET de leitura ao /api/seal/{domain}.
   Estilos inline (evita conflito com o CSS do site host). Auto dark/light. */
(function () {
  "use strict";
  var s = document.currentScript;
  if (!s) { var all = document.getElementsByTagName("script"); s = all[all.length - 1]; }
  var domain = (s && s.getAttribute("data-domain")) || "";
  var theme = (s && s.getAttribute("data-theme")) || "auto";   // auto | dark | light
  var size = (s && s.getAttribute("data-size")) || "compact";  // compact | full
  var base = "https://klarim.net";
  var mount = document.getElementById("klarim-seal");
  if (!mount || !domain) return;

  function isDark() {
    if (theme === "dark") return true;
    if (theme === "light") return false;
    try { return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches; }
    catch (e) { return false; }
  }
  function esc(v) { return String(v == null ? "" : v).replace(/[<>&"]/g, function (c) {
    return { "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]; }); }
  function fmtDate(d) {
    if (!d) return "";
    var p = String(d).split("-");
    return p.length === 3 ? p[2] + "/" + p[1] + "/" + p[0] : String(d);
  }

  function render(data) {
    var dark = isDark();
    var bg = dark ? "#161B22" : "#FFFFFF";
    var fg = dark ? "#E6EDF3" : "#1B2733";
    var mut = dark ? "#8B949E" : "#5B6673";
    var bd = dark ? "#30363D" : "#E2E6EA";
    var sem = { verde: "#00D26A", amarelo: "#F0C000", vermelho: "#F85149" };
    var dot = sem[data.semaphore] || "#8B949E";
    var url = (data.profile_url || (base + "/site/" + domain)) +
      "?utm_source=selo&utm_medium=widget";
    var score = (data.score != null) ? esc(data.score) + "/100" : "—";
    var priv = (data.privacy_score != null && data.privacy_total != null)
      ? '<div style="font-size:11px;color:' + mut + ';margin-top:1px;">Privacidade: ' +
        esc(data.privacy_score) + "/" + esc(data.privacy_total) + " indicadores</div>"
      : "";
    var when = data.last_scan ? '<div style="font-size:11px;color:' + mut +
      ';margin-top:1px;">Verificado em ' + esc(fmtDate(data.last_scan)) + "</div>" : "";
    var pad = size === "full" ? "12px 16px" : "8px 12px";
    mount.innerHTML =
      '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" ' +
      'style="display:inline-flex;align-items:center;gap:10px;text-decoration:none;' +
      "font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:" + bg +
      ";border:1px solid " + bd + ";border-radius:10px;padding:" + pad +
      ';box-shadow:0 1px 2px rgba(0,0,0,.06);">' +
      '<span aria-hidden="true" style="font-size:18px;">🔒</span>' +
      "<span>" +
      '<div style="font-size:12px;font-weight:700;color:' + fg + ';letter-spacing:.2px;">' +
      "Monitorado por " +
      '<span style="color:#FF6B35;">Klarim</span></div>' +
      '<div style="font-size:12px;color:' + fg + ';margin-top:1px;">' +
      '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' +
      dot + ';margin-right:4px;"></span>Score ' + score + "</div>" +
      priv + when +
      "</span></a>";
  }

  function fallback() {
    mount.innerHTML =
      '<a href="' + base + "/site/" + esc(domain) +
      '" target="_blank" rel="noopener noreferrer" ' +
      'style="display:inline-flex;align-items:center;gap:8px;text-decoration:none;' +
      "font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:12px;" +
      "font-weight:700;color:#1B2733;background:#fff;border:1px solid #E2E6EA;" +
      'border-radius:10px;padding:8px 12px;">🔒 Monitorado por ' +
      '<span style="color:#FF6B35;">Klarim</span></a>';
  }

  try {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", base + "/api/seal/" + encodeURIComponent(domain), true);
    xhr.timeout = 6000;
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      if (xhr.status === 200) {
        try { render(JSON.parse(xhr.responseText)); } catch (e) { fallback(); }
      } else { fallback(); }
    };
    xhr.ontimeout = fallback;
    xhr.onerror = fallback;
    xhr.send();
  } catch (e) { fallback(); }
})();
