import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api';

// Fix compliance urgente — no perfil público (/site/{domain}), os indicadores DETALHADOS
// de privacidade (✅/❌ por indicador + referência LGPD) só aparecem para visitantes
// LOGADOS. Deslogado vê apenas o resumo com cadeado (score/total). Expor as falhas de
// compliance de um site a qualquer visitante prejudica a empresa/o responsável técnico e
// vira vetor de engenharia social.
//
// O cookie de sessão é HttpOnly (JS não lê o JWT): a ilha consulta /api/account/me para
// detectar login e, se logado, busca os detalhes em /api/account/privacy/{domain}
// (endpoint autenticado — os detalhes NUNCA saem no SSR nem na API pública).
const CARD = 'rounded-2xl border border-slate-800 bg-slate-900/60 p-6';
const DISCLAIMER = 'Este é um diagnóstico técnico automatizado baseado em verificações passivas. Não constitui assessoria jurídica e não substitui a avaliação de um advogado ou Encarregado de Proteção de Dados (DPO). Para conformidade completa com a LGPD, consulte um profissional qualificado.';

export default function PrivacyPanel({ domain, score, total }) {
  // detail=null → deslogado ou ainda carregando (mostra resumo); {checks,...} → logado.
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    (async () => {
      const me = await apiGet('/account/me');
      if (!me.ok) return;                       // deslogado → mantém o resumo com cadeado
      const res = await apiGet(`/account/privacy/${encodeURIComponent(domain)}`);
      if (res.ok && res.data?.privacy) setDetail(res.data.privacy);
    })();
  }, [domain]);

  // Deslogado (ou carregando) → resumo com cadeado, SEM detalhes.
  if (!detail) {
    return (
      <div className={CARD}>
        <h2 className="text-lg font-bold text-white">🔒 Indicadores de privacidade: {score}/{total}</h2>
        <p className="mt-2 text-sm text-slate-400">Detalhes disponíveis para usuários logados.</p>
        <a href="/cadastrar" className="mt-3 inline-flex text-sm text-brand-400 hover:text-brand-300">
          Criar conta gratuita para ver os detalhes →
        </a>
      </div>
    );
  }

  // Logado → detalhes completos (✅/❌ por indicador + referência LGPD + disclaimer).
  const checks = Array.isArray(detail.checks) ? detail.checks : [];
  return (
    <div className={CARD}>
      <h2 className="text-lg font-bold text-white">Indicadores de privacidade: {detail.score ?? score}/{detail.total ?? total}</h2>
      <p className="mt-1 text-sm text-slate-400">Fatos técnicos observáveis por varredura passiva. Referência LGPD por indicador.</p>
      <ul className="mt-4 space-y-2">
        {checks.map((c, i) => (
          <li key={i} className="flex items-start gap-2 text-sm">
            <span aria-hidden="true">{c.status === 'PASS' ? '✅' : '❌'}</span>
            <span className="text-slate-200">{c.name}</span>
            <span className="ml-auto shrink-0 text-xs text-slate-500">{c.lgpd_ref}</span>
          </li>
        ))}
      </ul>
      <p className="mt-4 border-t border-slate-800 pt-3 text-xs leading-relaxed text-slate-500">
        ⚖️ {detail.disclaimer || DISCLAIMER}
      </p>
    </div>
  );
}
