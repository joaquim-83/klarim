// KL-90 UX (item 9) — "Explore": traz os dados de mercado (hoje escondidos no footer)
// para o dashboard. Cards compactos = ícone + título + subtítulo, links de navegação.
import { card } from './shared.js';

export default function ExploreSection({ sector, sectorLabel }) {
  const items = [
    sector && sector !== 'outro'
      ? { icon: '📊', title: 'Seu setor', sub: `Compare com ${sectorLabel || sector}`, href: `/setor/${sector}` }
      : { icon: '📊', title: 'Setores', sub: 'Segurança por segmento', href: '/setores' },
    { icon: '🏆', title: 'Ranking', sub: 'Sites mais seguros do Brasil', href: '/ranking' },
    { icon: '📈', title: 'Estatísticas', sub: 'Panorama de segurança web', href: '/estatisticas' },
    { icon: '⭐', title: 'Melhores', sub: 'Sites com score 100', href: '/melhores' },
  ];
  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">🧭 Inteligência de mercado</h2>
      <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {items.map((it) => (
          <a key={it.href} href={it.href}
            className="rounded-xl border border-slate-800 bg-slate-950/40 p-4 transition-colors hover:bg-slate-800/50">
            <span className="text-2xl" aria-hidden="true">{it.icon}</span>
            <p className="mt-1 text-sm font-semibold text-white">{it.title}</p>
            <p className="text-xs text-slate-400">{it.sub}</p>
          </a>
        ))}
      </div>
    </div>
  );
}
