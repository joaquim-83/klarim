// KL-152 P2 — navegação das docs do Security Gate (PURO, testável). Fonte única da sidebar +
// do sitemap. Cada item vira uma página em web/src/pages/docs/gate/{slug}.md.

export const DOCS_BASE = '/docs/gate';

export const DOCS_NAV = [
  {
    label: 'Plataformas',
    items: [
      { slug: 'github-actions', label: 'GitHub Actions' },
      { slug: 'gitlab-ci', label: 'GitLab CI' },
      { slug: 'bitbucket', label: 'Bitbucket' },
      { slug: 'jenkins', label: 'Jenkins' },
      { slug: 'manual', label: 'Manual / Terminal' },
    ],
  },
  { label: 'Referência', items: [{ slug: 'api', label: 'Referência da API' }] },
  { label: 'Ajuda', items: [{ slug: 'troubleshooting', label: 'Troubleshooting' }] },
];

export const DOCS_DEFAULT = 'github-actions';

export const docHref = (slug) => `${DOCS_BASE}/${slug}`;

export const isActiveDoc = (slug, active) => slug === active;

export const allDocSlugs = () => DOCS_NAV.flatMap((g) => g.items.map((i) => i.slug));
