// Middleware de autenticação do Astro (KL-51 f3). Protege /dashboard/*: lê o cookie
// de sessão e valida no backend (GET /account/me com o token). Sem/inválido → redirect
// para /entrar?redirect=... . O usuário validado fica em `Astro.locals.user`.
import { defineMiddleware } from 'astro:middleware';

const API = process.env.KLARIM_API_URL || 'http://api:8000';
const COOKIE = 'klarim_session';

export const onRequest = defineMiddleware(async (context, next) => {
  const { url, cookies, redirect, locals } = context;
  if (url.pathname === '/dashboard' || url.pathname.startsWith('/dashboard/')) {
    const token = cookies.get(COOKIE)?.value;
    const to = '/entrar?redirect=' + encodeURIComponent(url.pathname);
    if (!token) return redirect(to);
    try {
      const res = await fetch(`${API}/account/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return redirect(to);
      const data = await res.json();
      locals.user = data.user;
      locals.sitesCount = data.sites_count;
    } catch {
      return redirect(to);
    }
  }
  return next();
});
