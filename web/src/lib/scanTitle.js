// Fix de segurança (2026-07-21) — sanitiza o `?url=` antes de refletir no <title> do /scan.
// Não é XSS explorável (browsers não executam script em <title>, e o Astro já escapa), mas um
// site de segurança não pode refletir input não-sanitizado (credibilidade). Puro → testável
// com `node --test` (sem DOM), como scanView.js/layout.js.

// Extrai SÓ o hostname de um input de URL. Ignora protocolo/path/query/tags. '' se não houver
// hostname válido. Segunda camada: strip de tudo que não é char de hostname ([a-z0-9.-]).
export function safeScanDomain(input) {
  const raw = (input || '').trim();
  if (!raw) return '';
  let host = '';
  try {
    host = new URL(raw.includes('://') ? raw : `https://${raw}`).hostname;
  } catch {
    host = '';
  }
  host = host.replace(/^www\./, '').toLowerCase();
  // Defense-in-depth: só chars válidos de hostname; precisa ter um ponto p/ ser domínio.
  host = host.replace(/[^a-z0-9.-]/g, '');
  return host.includes('.') ? host : '';
}

// Título da página /scan a partir do `?url=`:
//   - hostname válido           → "Analisando {host}"
//   - input presente mas sem host → "Analisando um site"
//   - input vazio                → "Análise de segurança" (página genérica)
// O sufixo " · Klarim" é adicionado pelo layout Base.
export function scanTitle(input) {
  const raw = (input || '').trim();
  if (!raw) return 'Análise de segurança';
  const host = safeScanDomain(raw);
  return host ? `Analisando ${host}` : 'Analisando um site';
}
