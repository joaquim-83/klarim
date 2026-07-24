// KL-90 UX (item 5) — seção dedicada de MONITORAMENTO (o principal produto).
//  5a. status das vigílias ativas (GET /account/vigilias, filtrado ao site).
//  5b. o que está sendo monitorado (derivado das vigílias ativas + plano) e como as
//      notificações chegam. Honesto: reflete o estado REAL — não há endpoint de "salvar
//      preferências" ainda (persistência = follow-up de backend); mudar cobertura = mudar plano.
//  5c. boletim (frequência do plano).  5d. gancho para uma página dedicada futura.
import { useEffect, useState } from 'react';
import { card, outlineBtn, relDate } from './shared.js';
import MonitoringConfig from './MonitoringConfig.jsx';
import ProfileEditor from './ProfileEditor.jsx';

const ST = { ok: { i: '🟢', c: '#22c55e', t: 'OK' }, warning: { i: '🟡', c: '#eab308', t: 'Atenção' },
  alert: { i: '🟡', c: '#eab308', t: 'Atenção' }, critical: { i: '🔴', c: '#ef4444', t: 'Crítico' },
  error: { i: '🔴', c: '#ef4444', t: 'Erro' } };

// Itens monitoráveis (rótulo + tipo de vigília correspondente).
const MONITORABLE = [
  { tipo: 'ssl', label: 'Certificado SSL', hint: 'avisa antes do vencimento' },
  { tipo: 'score', label: 'Queda de score', hint: 'avisa se a nota cair' },
  { tipo: 'uptime', label: 'Site fora do ar', hint: 'checagem de disponibilidade' },
  { tipo: 'domain', label: 'Domínio expirando', hint: 'avisa antes de expirar' },
  { tipo: 'changes', label: 'Alteração no site/DNS', hint: 'detecta mudanças' },
  { tipo: 'phishing', label: 'Typosquat / phishing', hint: 'domínios que imitam o seu' },
];

function detail(v) {
  const d = v.last_data || {};
  if (v.tipo === 'ssl' && d.ssl_days_remaining != null) return `${d.ssl_days_remaining} dias restantes`;
  if (v.tipo === 'score' && d.detail) return d.detail;
  if (d.detail) return d.detail;
  return v.last_check_at ? `verificado ${relDate(v.last_check_at)}` : 'aguardando 1ª verificação';
}

export default function MonitoringSection({ domain, monitoring, targetId, canEditProfile = false, profile = {} }) {
  const [vigilias, setVigilias] = useState(null);
  const [modal, setModal] = useState('');   // '' | 'config' | 'profile'
  useEffect(() => {
    let alive = true;
    import('../../lib/api.js').then(({ apiGet }) =>
      apiGet('/account/vigilias').then(({ ok, data }) => {
        if (alive) setVigilias(ok ? (data.vigilias || []) : []);
      }));
    return () => { alive = false; };
  }, [domain]);

  const mine = (vigilias || []).filter((v) => v.site_domain === domain);
  const byType = Object.fromEntries(mine.map((v) => [v.tipo, v]));
  const m = monitoring || {};

  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">🛡️ Monitoramento</h2>
      <p className="mt-0.5 text-sm text-slate-400">
        {m.vigilias_active ? `${m.vigilias_active} vigília(s) ativa(s) · silenciosas 24/7` : 'Vigílias começam ao ativar o plano.'}
      </p>

      {/* 5a — status das vigílias ativas */}
      {mine.length > 0 && (
        <div className="mt-4 space-y-2">
          {mine.map((v) => {
            const st = ST[v.last_status] || ST.ok;
            const meta = MONITORABLE.find((x) => x.tipo === v.tipo);
            return (
              <div key={v.id} className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2.5">
                <span aria-hidden="true">{st.i}</span>
                <span className="flex-1">
                  <span className="block text-sm font-medium text-slate-100">{meta?.label || v.tipo}</span>
                  <span className="block text-xs text-slate-500">{detail(v)}</span>
                </span>
                <span className="text-xs font-semibold" style={{ color: st.c }}>{st.t}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* 5b — o que está sendo monitorado */}
      <div className="mt-5 border-t border-slate-800 pt-4">
        <h3 className="text-sm font-semibold text-slate-300">O que estamos monitorando</h3>
        <ul className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {MONITORABLE.map((it) => {
            const active = !!byType[it.tipo];
            return (
              <li key={it.tipo} className="flex items-center gap-2 text-sm">
                <span aria-hidden="true">{active ? '✅' : '⚪'}</span>
                <span className={active ? 'text-slate-200' : 'text-slate-500'}>
                  {it.label} <span className="text-xs text-slate-500">· {it.hint}</span>
                </span>
              </li>
            );
          })}
        </ul>
        <p className="mt-3 text-xs text-slate-500">
          A cobertura segue o seu plano. Para monitorar mais itens, <a href="/planos" className="text-brand-400 hover:text-brand-300">faça upgrade</a>.
        </p>
      </div>

      {/* notificações + boletim + ações (KL-97/98) */}
      <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-slate-800 pt-4 text-sm">
        <span className="text-slate-300">📧 Alertas por e-mail{m.technician_linked ? ' · técnico vinculado' : ''}</span>
        <span className="text-slate-300">📰 Boletim: <strong className="text-white">{m.bulletin_frequency || 'nenhum'}</strong></span>
        <div className="ml-auto flex flex-wrap gap-2">
          {targetId && <button type="button" onClick={() => setModal('config')} className={outlineBtn}>⚙️ Configurar</button>}
          {targetId && canEditProfile && <button type="button" onClick={() => setModal('profile')} className={outlineBtn}>✏️ Editar perfil</button>}
        </div>
      </div>

      {modal === 'config' && (
        <MonitoringConfig targetId={targetId} domain={domain} onClose={() => setModal('')} />
      )}
      {modal === 'profile' && (
        <ProfileEditor targetId={targetId} domain={domain} initial={profile} onClose={() => setModal('')} />
      )}
    </div>
  );
}
