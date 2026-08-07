// KL-152 P2 — botão "Copiar" nos blocos de código das docs (progressive enhancement, CSP 'self').
// As páginas funcionam sem JS; isto só adiciona a conveniência de copiar. Sem deps.
(function () {
  function enhance() {
    var pres = document.querySelectorAll('.docs-prose pre');
    for (var i = 0; i < pres.length; i++) {
      var pre = pres[i];
      if (pre.parentNode && pre.parentNode.classList && pre.parentNode.classList.contains('docs-pre-wrap')) continue;
      var wrap = document.createElement('div');
      wrap.className = 'docs-pre-wrap';
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'docs-copy-btn';
      btn.textContent = 'Copiar';
      (function (b, p) {
        b.addEventListener('click', function () {
          var code = p.innerText;
          function done() { b.textContent = 'Copiado ✓'; setTimeout(function () { b.textContent = 'Copiar'; }, 2000); }
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(code).then(done).catch(function () {});
          }
        });
      })(btn, pre);
      wrap.appendChild(btn);
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', enhance);
  else enhance();
})();
