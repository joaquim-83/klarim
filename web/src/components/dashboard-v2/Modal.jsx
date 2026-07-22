// KL-90 UX — modal base (overlay + card centralizado + fechar). Theme-aware.
export default function Modal({ title, onClose, wide = false, children }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-4"
      role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className={`relative z-10 my-8 w-full ${wide ? 'max-w-lg' : 'max-w-md'} rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-2xl`}>
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-bold text-white">{title}</h3>
          <button type="button" onClick={onClose} aria-label="Fechar"
            className="min-h-[44px] min-w-[44px] text-slate-500 hover:text-slate-300">✕</button>
        </div>
        <div className="mt-3">{children}</div>
      </div>
    </div>
  );
}
