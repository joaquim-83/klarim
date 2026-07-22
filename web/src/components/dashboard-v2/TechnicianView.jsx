// KL-90 — Dashboard TÉCNICO de um site de cliente (modo técnico). O profissional de TI vê
// os dados técnicos completos (48 checks + evidência + fix por plataforma + PDF técnico) e a
// vigília do dono (read-only), NUNCA a conta do dono. Renderizado quando data.technician_mode.
import { useState } from 'react';
import { apiPut } from '../../lib/api.js';
import { card } from './shared.js';
import ScoreCard from './ScoreCard.jsx';
import CategoryBar from './CategoryBar.jsx';
import RisksList from './RisksList.jsx';
import MonitoringSection from './MonitoringSection.jsx';
import ScoreHistory from './ScoreHistory.jsx';

export default function TechnicianView({ data, scanning, onScan, onToast }) {
  const site = data.site;
  const tid = data.selected_site_id;
  const [alerts, setAlerts] = useState(data.can_receive_alerts !== false);
  const [saving, setSaving] = useState(false);

  async function toggleAlerts() {
    const next = !alerts;
    setAlerts(next); setSaving(true);
    const { ok } = await apiPut('/account/technician/notifications', { target_id: tid, enabled: next });
    setSaving(false);
    if (!ok) { setAlerts(!next); onToast('Não foi possível salvar a preferência.'); }
    else onToast(next ? '🔔 Você receberá os alertas deste site' : '🔕 Alertas deste site desativados');
  }

  return (
    <div className="space-y-6">
      {/* banner — modo técnico */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand-500/40 bg-brand-500/10 px-4 py-3">
        <p className="text-sm text-slate-200">
          🔧 <strong className="text-white">Visualizando como técnico</strong> ·{' '}
          <span className="font-mono text-slate-200">{site.domain}</span> ·{' '}
          Dono: <span className="text-slate-400">{data.owner_email}</span>
        </p>
        <a href="/dashboard" className="inline-flex min-h-[44px] items-center text-sm font-semibold text-brand-300 hover:text-brand-200">
          ← Voltar para meus clientes
        </a>
      </div>

      <ScoreCard site={site} benchmark={data.benchmark} scanning={scanning}
        onScan={onScan} onToast={onToast} technician />

      {/* item 4 — receber cópia dos alertas do site do cliente */}
      <div className={`${card} flex flex-wrap items-center justify-between gap-3`}>
        <div>
          <p className="text-sm font-semibold text-white">🔔 Receber alertas deste site</p>
          <p className="text-xs text-slate-400">Cópia dos alertas de vigília do cliente (SSL expirando, queda de score, uptime).</p>
        </div>
        <button type="button" onClick={toggleAlerts} disabled={saving} aria-pressed={alerts}
          className={`relative h-7 w-12 shrink-0 rounded-full transition-colors ${alerts ? 'bg-brand-500' : 'bg-slate-700'} disabled:opacity-60`}>
          <span className={`absolute top-1 h-5 w-5 rounded-full bg-white transition-all ${alerts ? 'left-6' : 'left-1'}`} />
        </button>
      </div>

      <MonitoringSection domain={site.domain} monitoring={data.monitoring} />

      {/* checks técnicos (evidência primária) */}
      <CategoryBar categories={data.categories} siteType={site.site_type} technical onForward={() => {}} />

      {/* prioridades (linguagem de negócio, p/ o técnico relatar ao cliente) */}
      <div className={card}>
        <h3 className="text-lg font-bold text-white">⚠️ Prioridades para o cliente</h3>
        <div className="mt-3">
          <RisksList risks={data.risks} siteType={site.site_type} onForward={() => {}} />
        </div>
      </div>

      <ScoreHistory history={data.score_history} />
    </div>
  );
}
