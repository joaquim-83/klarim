// KL-157 — detecção de sessão no SERVIDOR (Astro SSR). Lê o cookie de sessão (`klarim_session`,
// HttpOnly — JS do cliente NÃO lê) e valida no backend (`GET /account/me` com o Bearer). Mesmo
// padrão do `src/middleware.js`, mas usável em páginas que NÃO estão sob /dashboard/* (ex.:
// /cadastrar) para redirecionar o usuário logado. Usar SÓ em páginas `no-store` (não cacheadas):
// /cadastrar e /entrar são no-store no nginx; /security-gate é cacheada (max-age=300) → lá a
// detecção fica no cliente (ilha GateLandingCTA).
const API = process.env.KLARIM_API_URL || 'http://api:8000';
const COOKIE = 'klarim_session';

export async function fetchSessionUser(cookies) {
  const token = cookies?.get?.(COOKIE)?.value;
  if (!token) return null;
  try {
    const r = await fetch(`${API}/account/me`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) return null;
    const data = await r.json();
    return data.user || null;
  } catch {
    return null;   // backend fora / token inválido → trata como deslogado (mostra o form)
  }
}

// Destino do redirect quando um usuário LOGADO cai numa página de cadastro/entrada.
export function loggedInRedirect(isDev, fallback = '/dashboard') {
  return isDev ? '/dashboard/gate' : (fallback || '/dashboard');
}
