// Cliente HTTP das ilhas React (KL-51 f3). Sempre same-origin (/api/... → Nginx →
// FastAPI) com `credentials: 'include'` para enviar/receber o cookie de sessão.

const API = '/api';

async function req(method, path, body) {
  const opts = { method, credentials: 'include', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res, data;
  try {
    res = await fetch(API + path, opts);
  } catch {
    return { ok: false, status: 0, data: {}, error: 'Erro de conexão. Tente novamente.' };
  }
  try {
    data = await res.json();
  } catch {
    data = {};
  }
  return { ok: res.ok, status: res.status, data, error: res.ok ? '' : (data.detail || 'Erro inesperado.') };
}

export const apiGet = (path) => req('GET', path);
export const apiPost = (path, body) => req('POST', path, body || {});
export const apiDelete = (path) => req('DELETE', path);
