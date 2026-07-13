export const prerender = false;

// Sitemap dinâmico (KL-51 f4): páginas estáticas + 1 URL por perfil público
// (/site/{dominio}). Os domínios vêm do backend (só scanned/alerted com site_profile).
const SITE = 'https://klarim.net';
const API = process.env.KLARIM_API_URL || 'http://api:8000';

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export async function GET() {
  let domains = [];
  try {
    const res = await fetch(`${API}/public/sitemap-domains`, { signal: AbortSignal.timeout(8000) });
    if (res.ok) domains = (await res.json()).domains || [];
  } catch { /* backend fora → só as páginas estáticas */ }

  const staticUrls = [
    `<url><loc>${SITE}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>`,
    `<url><loc>${SITE}/sobre</loc><changefreq>monthly</changefreq><priority>0.5</priority></url>`,
    `<url><loc>${SITE}/scan</loc><changefreq>weekly</changefreq><priority>0.6</priority></url>`,
  ];
  const profileUrls = domains
    .filter((d) => d && d.domain)
    .map((d) =>
      `<url><loc>${SITE}/site/${esc(d.domain)}</loc>` +
      (d.lastmod ? `<lastmod>${esc(d.lastmod)}</lastmod>` : '') +
      `<changefreq>monthly</changefreq><priority>0.7</priority></url>`);

  const xml =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    [...staticUrls, ...profileUrls].join('\n') +
    `\n</urlset>\n`;

  return new Response(xml, {
    headers: { 'Content-Type': 'application/xml; charset=utf-8', 'Cache-Control': 'public, max-age=3600' },
  });
}
