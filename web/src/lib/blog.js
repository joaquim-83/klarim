// KL-133 — helpers do blog. `renderMarkdown` roda no SSR (Node): marked → sanitize-html
// (allowlist estrita; <script>/<iframe>/on*= são removidos — prevenção de XSS, rule 7). Os
// helpers de apresentação (data/categoria/sidebar) são puros/testáveis.
import { marked } from 'marked';
import sanitizeHtml from 'sanitize-html';

const SANITIZE_OPTS = {
  allowedTags: [
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'a', 'ul', 'ol', 'li', 'blockquote',
    'strong', 'em', 'del', 'code', 'pre', 'hr', 'br', 'img', 'span',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
  ],
  allowedAttributes: {
    a: ['href', 'title', 'target', 'rel'],
    img: ['src', 'alt', 'title', 'width', 'height', 'loading'],
    code: ['class'], span: ['class'], td: ['align'], th: ['align'],
  },
  allowedSchemes: ['http', 'https', 'mailto'],
  // Links externos com rel de segurança (evita window.opener + não passa PageRank).
  transformTags: {
    a: sanitizeHtml.simpleTransform('a', { rel: 'noopener noreferrer nofollow' }, true),
  },
};

// Markdown (string) → HTML sanitizado (string). Suporta tabelas, código, imagens,
// headings, listas, bold/italic, links. Qualquer <script>/<iframe>/handler é removido.
export function renderMarkdown(md) {
  if (!md) return '';
  const raw = marked.parse(String(md), { async: false, gfm: true, breaks: false });
  return sanitizeHtml(raw, SANITIZE_OPTS);
}

// --- helpers puros de apresentação --------------------------------------------------- #

const CATEGORY = {
  seguranca: 'Segurança',
  lgpd: 'LGPD',
  dados: 'Dados',
  setor: 'Setores',
  tutorial: 'Tutorial',
};

export function categoryLabel(cat) {
  return CATEGORY[cat] || 'Segurança';
}

export function formatBlogDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: 'long', year: 'numeric' });
  } catch {
    return '';
  }
}

// Links da sidebar por categoria (estáticos — conduzem ao produto/conteúdo relacionado).
export function sidebarLinks(category) {
  const base = [
    { href: '/scan', label: 'Verificar a segurança do seu site' },
    { href: '/setores', label: 'Segurança por setor' },
  ];
  const byCat = {
    setor: [{ href: '/setor/tecnologia', label: 'Setor: Tecnologia' },
            { href: '/setor/ecommerce', label: 'Setor: E-commerce' },
            { href: '/ranking', label: 'Ranking de segurança' }],
    dados: [{ href: '/estatisticas', label: 'Estatísticas da plataforma' },
            { href: '/melhores', label: 'Sites mais seguros' }],
    seguranca: [{ href: '/metodologia', label: 'Como o Klarim avalia' },
                { href: '/melhores', label: 'Sites mais seguros' }],
    lgpd: [{ href: '/metodologia', label: 'Metodologia e base legal' }],
    tutorial: [{ href: '/metodologia', label: 'Metodologia' }],
  };
  return [...base, ...(byCat[category] || byCat.seguranca)].slice(0, 5);
}
