// KL-90 P2/UX — Checklist: ações derivadas dos riscos + perfil/selo/share.
// Conteúdo "puro" (sem card/header) — vai dentro de um Collapsible (item 6). Concluídos riscados.
export default function Checklist({ items }) {
  const list = (items || []).slice(0, 5);
  return (
    <div>
      {list.length === 0 ? (
        <p className="text-sm text-slate-400">Tudo em dia por aqui 👏</p>
      ) : (
        <ul className="space-y-1">
          {list.map((item) => (
            <li key={item.id} className="flex items-start gap-3 rounded-lg p-2">
              <span aria-hidden="true">{item.completed ? '✅' : '☐'}</span>
              <span className={`flex-1 text-sm ${item.completed ? 'text-slate-500 line-through' : 'text-slate-200'}`}>
                {item.label}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
