// KL-90 P2 — EmptyDashboard: usuário sem site. CTA para adicionar + checklist reduzido.
import { card, brandBtn } from './shared.js';

export default function EmptyDashboard({ data }) {
  const checklist = data.checklist || [];
  return (
    <div className="space-y-6">
      <div className={`${card} border-brand-500/30 bg-brand-500/5 text-center`}>
        <h2 className="text-2xl font-bold text-white">Adicione seu primeiro site</h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-400">
          Comece a monitorar a segurança do seu site — score, alertas e vigília contínua.
        </p>
        <form action="/scan" method="GET" className="mx-auto mt-4 flex max-w-lg flex-col gap-2 sm:flex-row">
          <input type="text" name="url" required placeholder="🔍 seusite.com.br"
            className="w-full rounded-xl border border-slate-700 bg-slate-800/80 px-4 py-3 text-base text-white placeholder:text-slate-500 outline-none focus:border-brand-500" />
          <button type="submit" className={brandBtn}>Adicionar site →</button>
        </form>
      </div>

      {checklist.length > 0 && (
        <div className={card}>
          <h3 className="text-lg font-bold text-white">📋 Primeiros passos</h3>
          <ul className="mt-3 space-y-1">
            {checklist.map((item) => (
              <li key={item.id} className="flex items-start gap-3 rounded-lg p-2">
                <span aria-hidden="true">{item.completed ? '✅' : '☐'}</span>
                <span className={`flex-1 text-sm ${item.completed ? 'text-slate-500 line-through' : 'text-slate-200'}`}>
                  {item.label}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
