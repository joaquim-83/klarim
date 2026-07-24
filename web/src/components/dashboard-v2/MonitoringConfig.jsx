// KL-97 — modal de configuração do monitoramento de um site: toggles de vigília (bloqueadas
// pelo plano com cadeado), threshold do score, e preferências de notificação. Salva via
// PUT /account/sites/{id}/monitoring + PUT /account/notification-preferences.
import { useState, useEffect } from 'react';
import { apiGet, apiPut } from '../../lib/api.js';
import Modal from './Modal.jsx';
import { brandBtn, outlineBtn } from './shared.js';

const VIGILIA_LABEL = {
  ssl: 'SSL / Certificado', domain: 'Domínio (expiração)', score: 'Score de segurança',
  email: 'E-mail (SPF/DKIM)', reputation: 'Reputação (blocklists)', uptime: 'Disponibilidade',
  changes: 'Mudanças no site', phishing: 'Typosquat / Phishing',
};
const STATUS_ICON = { ok: '✅', warning: '⚠️', critical: '❌', error: '❌' };
const FREQ = [['off', 'Desligado'], ['monthly', 'Mensal'], ['weekly', 'Semanal'],
  ['daily', 'Diário'], ['immediate', 'Imediato']];
const PLAN_LABEL = { pro: 'Pro', agency: 'Agency' };

export default function MonitoringConfig({ targetId, domain, onClose }) {
  const [data, setData] = useState(null);
  const [prefs, setPrefs] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    Promise.all([
      apiGet(`/account/sites/${targetId}/monitoring`),
      apiGet('/account/notification-preferences'),
    ]).then(([mo, pr]) => {
      if (!alive) return;
      if (mo.ok) setData(mo.data); else setErr(mo.error || 'Falha ao carregar.');
      if (pr.ok) setPrefs(pr.data);
    });
    return () => { alive = false; };
  }, [targetId]);

  const upd = (tipo, patch) => setData((d) => ({
    ...d, vigilias: d.vigilias.map((v) => (v.tipo === tipo ? { ...v, ...patch } : v)),
  }));

  async function save() {
    setBusy(true); setMsg(''); setErr('');
    const payload = {};
    for (const v of data.vigilias) {
      if (!v.configurable) continue;
      payload[v.tipo] = { enabled: !!v.enabled };
      if (v.tipo === 'score' && v.threshold) payload[v.tipo].threshold = Number(v.threshold);
    }
    const r = await apiPut(`/account/sites/${targetId}/monitoring`, { vigilias: payload });
    if (!r.ok) { setErr(r.error || 'Falha ao salvar as vigílias.'); setBusy(false); return; }
    setData(r.data);
    window.klarimTrack?.('vigilia_toggled', { domain }, '');
    if (prefs) {
      const pr = await apiPut('/account/notification-preferences', prefs);
      if (pr.ok) { setPrefs(pr.data); window.klarimTrack?.('bulletin_frequency_changed', { frequency: prefs.bulletin_frequency }, ''); }
    }
    setMsg('Preferências atualizadas ✓'); setBusy(false);
  }

  return (
    <Modal title={`Configurar ${domain}`} onClose={onClose} wide>
      {err && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">{err}</p>}
      {!data ? <p className="text-sm text-slate-400">Carregando…</p> : (
        <div className="space-y-5">
          <div>
            <h4 className="text-sm font-semibold text-white">Vigílias</h4>
            <ul className="mt-2 space-y-2">
              {data.vigilias.map((v) => (
                <li key={v.tipo} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 px-3 py-2">
                  <div className="min-w-0">
                    <span className="text-sm text-slate-200">{STATUS_ICON[v.last_status] || '•'} {VIGILIA_LABEL[v.tipo] || v.tipo}</span>
                    {!v.configurable && (
                      <span className="ml-2 text-xs text-slate-500">🔒 Disponível no {PLAN_LABEL[v.requires_plan] || 'Pro'}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {v.configurable && v.tipo === 'score' && v.enabled && (
                      <select value={v.threshold || 5} onChange={(e) => upd('score', { threshold: e.target.value })}
                        className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200">
                        {[5, 10, 20].map((n) => <option key={n} value={n}>-{n} pts</option>)}
                      </select>
                    )}
                    <button type="button" disabled={!v.configurable}
                      onClick={() => upd(v.tipo, { enabled: !v.enabled })}
                      className={`min-h-[28px] rounded-full px-3 text-xs font-semibold ${
                        v.enabled ? 'bg-brand-500 text-[var(--accent-text)]' : 'bg-slate-700 text-slate-300'
                      } ${v.configurable ? '' : 'cursor-not-allowed opacity-50'}`}>
                      {v.enabled ? 'Ativo' : 'Off'}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {prefs && (
            <div>
              <h4 className="text-sm font-semibold text-white">Notificações</h4>
              <div className="mt-2 space-y-2 text-sm text-slate-200">
                <label className="flex items-center justify-between gap-3">
                  <span>Boletim de segurança</span>
                  <select value={prefs.bulletin_frequency || ''} onChange={(e) => setPrefs((p) => ({ ...p, bulletin_frequency: e.target.value || null }))}
                    className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs">
                    <option value="">Padrão do plano</option>
                    {FREQ.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </label>
                {[['notify_vigilia', 'Alertas de vigília'], ['notify_bulletin', 'Boletim por e-mail'], ['notify_news', 'Novidades do Klarim']].map(([k, l]) => (
                  <label key={k} className="flex items-center justify-between gap-3">
                    <span>{l}</span>
                    <input type="checkbox" checked={!!prefs[k]} onChange={(e) => setPrefs((p) => ({ ...p, [k]: e.target.checked }))} />
                  </label>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center justify-between gap-3">
            {msg && <span className="text-sm text-green-400">{msg}</span>}
            <div className="ml-auto flex gap-2">
              <button type="button" onClick={onClose} className={outlineBtn}>Fechar</button>
              <button type="button" disabled={busy} onClick={save} className={brandBtn}>
                {busy ? 'Salvando…' : 'Salvar'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
