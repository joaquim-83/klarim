// KL-134 P2 — ilha interativa das micro-ferramentas (client:load). Input + "Verificar" → chama o
// endpoint público (P1), renderiza o resultado inline (sem redirect) e o CTA para o scanner
// completo. Mobile-first: input e botão empilham em 375px, ficam lado a lado em sm+.
import { useState } from 'react';
import { toolBySlug, buildToolUrl, parseToolError } from '../../lib/tools.js';
import ToolResult from './Results.jsx';
import ToolCta from './ToolCta.jsx';

export default function ToolPage({ tool }) {
  const meta = toolBySlug(tool);
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState(null);

  if (!meta) return null;

  async function run(e) {
    if (e) e.preventDefault();
    const v = value.trim();
    if (!v || loading) return;
    setLoading(true);
    setError('');
    setData(null);
    try {
      const res = await fetch(buildToolUrl(meta.endpoint, meta.paramName, v));
      let body = null;
      try { body = await res.json(); } catch { /* corpo não-JSON */ }
      if (!res.ok) {
        setError(parseToolError(res.status, body));
      } else {
        setData(body);
      }
    } catch {
      setError('Não foi possível concluir a análise. Verifique sua conexão e tente novamente.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <form onSubmit={run} class="flex w-full flex-col gap-3 sm:flex-row">
        <label htmlFor="tool-input" class="sr-only">{meta.placeholder}</label>
        <input
          id="tool-input"
          type="text"
          inputMode="url"
          autoComplete="url"
          spellCheck="false"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={meta.placeholder}
          class="h-12 w-full flex-1 rounded-xl border border-slate-700 bg-slate-800/80 px-4 text-base text-white placeholder:text-slate-500 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30"
        />
        <button
          type="submit"
          disabled={loading || !value.trim()}
          class="inline-flex min-h-[48px] w-full items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 text-base font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
        >
          {loading ? (
            <>
              <span class="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden="true" />
              Analisando…
            </>
          ) : 'Verificar'}
        </button>
      </form>

      {error && (
        <p role="alert" class="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </p>
      )}

      {data && (
        <div class="mt-6">
          <ToolResult tool={tool} data={data} />
          <ToolCta domain={data.domain || value.trim()} />
        </div>
      )}
    </div>
  );
}
