// KL-134 P2 — CTA final (após o resultado de qualquer ferramenta): conduz ao scanner COMPLETO
// (não ao cadastro). Reutilizável. `domain` pré-preenche o scanner; sempre há o link para as
// outras ferramentas.
import { fullScanHref } from '../../lib/tools.js';

export default function ToolCta({ domain }) {
  return (
    <div class="mt-6 rounded-2xl border border-brand-500/40 bg-gradient-to-br from-brand-500/15 to-slate-900/40 p-6 text-center">
      <p class="text-sm text-slate-300">
        Esta é uma das <strong class="text-white">86 verificações</strong> que a Klarim faz gratuitamente.
      </p>
      <p class="mt-1 text-lg font-bold text-white">Veja a análise completa do seu site</p>
      <a href={fullScanHref(domain)}
        class="mt-4 inline-flex min-h-[48px] w-full items-center justify-center rounded-xl bg-brand-500 px-6 text-base font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 active:scale-[0.98] sm:w-auto">
        Verificar meu site — completo e gratuito →
      </a>
      <p class="mt-3">
        <a href="/ferramentas" class="text-sm font-medium text-brand-400 hover:underline">Outras ferramentas →</a>
      </p>
    </div>
  );
}
