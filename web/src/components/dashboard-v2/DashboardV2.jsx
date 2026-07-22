// KL-90 — Dashboard v2 (orquestrador). Superset do dashboard de produção: reusa os
// componentes existentes (PlanSection = checkout PIX/QR; TechnicianSection = convite/
// revogar/laudo) e restaura as features perdidas na reescrita (selo, remover site,
// dashboard do técnico, has_other_owner). Rota principal (/dashboard).
import { useState, useEffect, useCallback } from 'react';
import { apiGet, apiDelete } from '../../lib/api.js';
import { card } from './shared.js';
import PlanSection from '../account/PlanSection.jsx';          // reg 3 — checkout PIX/QR completo
import TechnicianSection from '../account/TechnicianSection.jsx'; // reg 2+7 — convite/revogar/laudo
import MonitoredSitesPanel from './MonitoredSitesPanel.jsx';
import ScoreCard from './ScoreCard.jsx';
import MonitoringSection from './MonitoringSection.jsx';
import SealSection from './SealSection.jsx';                    // reg 1 — selo
import CategoryBar from './CategoryBar.jsx';
import Collapsible from './Collapsible.jsx';
import RisksList from './RisksList.jsx';
import Checklist from './Checklist.jsx';
import ScoreHistory from './ScoreHistory.jsx';
import EmptyDashboard from './EmptyDashboard.jsx';
import TechnicianClients from './TechnicianClients.jsx';        // reg 5 — dashboard do técnico
import ConfirmEmailBanner from './ConfirmEmailBanner.jsx';      // regressão: banner confirmar e-mail
import Modal from './Modal.jsx';
import AddSiteModal from './AddSiteModal.jsx';

export default function DashboardV2({ user = {} }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [toast, setToast] = useState('');
  const [techModal, setTechModal] = useState(false);
  const [addModal, setAddModal] = useState(false);
  const [upgradeParam] = useState(() =>
    (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('upgrade') : '') || '');

  const isTech = user.role === 'technician' || user.role === 'both';

  const load = useCallback(async (siteId, keepData) => {
    setError(''); if (!keepData) setData(null);
    const q = siteId ? `?site_id=${siteId}` : '';
    const { ok, data: d, error: err } = await apiGet(`/account/dashboard-summary${q}`);
    if (ok) { setData(d); if (d.selected_site_id) setSelectedId(d.selected_site_id); }
    else setError(err || 'Não foi possível carregar o dashboard.');
  }, []);

  useEffect(() => { load(null); }, [load]);
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(''), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  function onSelect(id) { setSelectedId(id); load(id); }

  async function onScan() {
    if (!data?.site?.domain) return;
    setScanning(true);
    await apiGet(`/scan/result?url=${encodeURIComponent(data.site.domain)}&refresh=1`);
    setScanning(false);
    setToast('✅ Análise atualizada');
    load(selectedId, true);
  }

  // reg 4 — remover site do monitoramento (com confirmação).
  async function onRemove(site) {
    if (!window.confirm(`Remover ${site.domain} do monitoramento? As vigílias desse site serão desativadas (os dados e o histórico são mantidos).`)) return;
    const { ok } = await apiDelete(`/account/sites/${site.id}`);
    if (ok) { setToast(`${site.domain} removido do monitoramento`); load(null); }
    else setToast('Não foi possível remover o site.');
  }

  if (error) {
    return (
      <div className={`${card} mx-auto max-w-md text-center`}>
        <p className="text-slate-200">{error}</p>
        <button type="button" onClick={() => load(selectedId)}
          className="mt-4 inline-flex min-h-[44px] items-center rounded-xl border border-slate-700 px-5 text-sm font-semibold text-slate-200 hover:bg-slate-800">
          Tentar novamente
        </button>
      </div>
    );
  }
  if (data === null) return <Skeleton />;

  // Sem site próprio: técnico vê os clientes; usuário comum vê o onboarding.
  if (!data.has_site) {
    return (
      <div className="space-y-6">
        <ConfirmEmailBanner user={user} />
        <TechnicianClients isTech={isTech} />
        <EmptyDashboard data={data} />
        {addModal && <AddSiteModal onClose={() => setAddModal(false)} onAdded={() => load(null)} />}
        <Toast toast={toast} />
      </div>
    );
  }

  const site = data.site;
  const risks = data.risks || [];
  const pendingChecklist = (data.checklist || []).filter((i) => !i.completed).length;
  const targetId = site.target_id || selectedId;

  return (
    <div className="space-y-6">
      <ConfirmEmailBanner user={user} />
      <TechnicianClients isTech={isTech} />

      <div className="lg:flex lg:gap-6">
        <aside className="lg:w-72 lg:shrink-0">
          <MonitoredSitesPanel sites={data.sites || []} selectedId={selectedId}
            onSelect={onSelect} onAddSite={() => setAddModal(true)} onRemove={onRemove} />
        </aside>

        <div className="mt-6 space-y-6 lg:mt-0 lg:min-w-0 lg:flex-1">
          {!site.is_online && (
            <Banner tone="warn">⚠️ Seu site não está respondendo. Último scan: {relStr(site.last_scan_at)}.</Banner>
          )}
          {site.is_online && site.score === 100 && (
            <Banner tone="ok">🎉 Score perfeito! Compartilhe com seus clientes o selo de segurança do seu site.</Banner>
          )}

          <ScoreCard site={site} benchmark={data.benchmark} scanning={scanning}
            onScan={onScan} onToast={setToast} onLinkTechnician={() => setTechModal(true)} />

          <MonitoringSection domain={site.domain} monitoring={data.monitoring} />

          {/* reg 1 — selo Klarim */}
          <SealSection domain={site.domain} planName={(data.plan || {}).name} />

          <CategoryBar categories={data.categories} siteType={site.site_type}
            onForward={() => setTechModal(true)} />

          {/* item 6 + affordance — riscos aberto por padrão, checklist recolhido */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Collapsible title="⚠️ Riscos para o seu negócio" count={risks.length ? `${risks.length}` : '0'} defaultOpen>
              <RisksList risks={risks} siteType={site.site_type} onForward={() => setTechModal(true)} />
            </Collapsible>
            <Collapsible title="📋 O que fazer agora" count={pendingChecklist ? `${pendingChecklist}` : '0'}>
              <Checklist items={data.checklist} />
            </Collapsible>
          </div>

          <ScoreHistory history={data.score_history} />

          {/* reg 3 — plano com checkout PIX/QR (componente de produção reusado) */}
          <PlanSection initialUpgrade={upgradeParam} />
        </div>
      </div>

      {/* reg 2+7 — modal técnico: convite/lista/revogar + laudo compartilhável */}
      {techModal && (
        <Modal title="🔧 Técnico responsável" wide onClose={() => setTechModal(false)}>
          <TechnicianSection targetId={targetId} />
        </Modal>
      )}
      {addModal && (
        <AddSiteModal onClose={() => setAddModal(false)}
          onAdded={() => { setToast('✅ Site adicionado ao monitoramento'); load(null); }} />
      )}
      <Toast toast={toast} />
    </div>
  );
}

function relStr(s) {
  try { return new Date(s.endsWith('Z') ? s : s + 'Z').toLocaleDateString('pt-BR'); } catch { return 'há algum tempo'; }
}

function Toast({ toast }) {
  if (!toast) return null;
  return (
    <div className="fixed inset-x-0 bottom-6 z-[60] mx-auto w-fit rounded-xl border border-slate-700 bg-slate-900 px-5 py-3 text-sm text-white shadow-xl">
      {toast}
    </div>
  );
}

function Banner({ tone, children }) {
  const cls = tone === 'ok'
    ? 'border-green-500/40 bg-green-500/10 text-green-300'
    : 'border-yellow-500/40 bg-yellow-500/10 text-yellow-200';
  return <div className={`rounded-xl border px-4 py-3 text-sm ${cls}`}>{children}</div>;
}

function Skeleton() {
  const box = 'rounded-2xl border border-slate-800 bg-slate-900/60 animate-pulse';
  return (
    <div className="lg:flex lg:gap-6">
      <div className={`${box} h-64 lg:w-72 lg:shrink-0`} />
      <div className="mt-6 space-y-6 lg:mt-0 lg:flex-1">
        <div className={`${box} h-56`} />
        <div className={`${box} h-40`} />
        <div className={`${box} h-28`} />
      </div>
    </div>
  );
}
