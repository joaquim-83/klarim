import { useState } from 'react';

// Formulário de contato (KL-58 fix): POST client-side para /api/contact (FastAPI, sem
// a proteção CSRF do Astro SSR — que barrava o POST atrás do Cloudflare com
// "Cross-site POST form submissions are forbidden"). Feedback inline, honeypot anti-bot.
const field =
  'w-full rounded-xl border border-slate-700 bg-slate-800/80 px-4 py-3 text-base text-white ' +
  'placeholder:text-slate-500 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30';

export default function ContactForm() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [website, setWebsite] = useState(''); // honeypot: invisível para humanos
  const [status, setStatus] = useState('idle'); // idle | sending | sent | error
  const [error, setError] = useState('');

  async function submit(e) {
    e.preventDefault();
    setError('');
    if (website) { setStatus('sent'); return; } // bot preencheu o honeypot → finge sucesso
    if (!email || !message) { setError('Preencha e-mail e mensagem.'); return; }
    setStatus('sending');
    try {
      // O Nginx injeta o X-Real-IP (o rate limit da API usa o IP real do cliente).
      const res = await fetch('/api/contact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, message }),
      });
      if (res.ok) {
        setStatus('sent');
        setName(''); setEmail(''); setMessage('');
      } else if (res.status === 429) {
        setStatus('error');
        setError('Muitas mensagens em pouco tempo. Tente novamente mais tarde.');
      } else {
        const d = await res.json().catch(() => ({}));
        setStatus('error');
        setError(d?.detail || 'Não foi possível enviar. Tente novamente.');
      }
    } catch {
      setStatus('error');
      setError('Erro de conexão. Tente novamente em instantes.');
    }
  }

  if (status === 'sent') {
    return (
      <div className="mt-8 rounded-2xl border border-brand-500/30 bg-brand-500/10 p-6">
        <p className="font-semibold text-brand-300">Mensagem enviada!</p>
        <p className="mt-1 text-sm text-slate-300">Respondemos em até 48h.</p>
        <a href="/" className="mt-4 inline-block text-sm text-brand-400 hover:text-brand-300">← Voltar ao início</a>
      </div>
    );
  }

  return (
    <div>
      <p className="mt-3 text-slate-400">Dúvidas, sugestões ou suporte? Escreva pra gente.</p>

      {error && (
        <p className="mt-6 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</p>
      )}

      <form onSubmit={submit} className="mt-6 flex flex-col gap-4" noValidate>
        <div>
          <label htmlFor="name" className="mb-1.5 block text-sm text-slate-300">Nome</label>
          <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)}
            autoComplete="name" className={field} />
        </div>
        <div>
          <label htmlFor="email" className="mb-1.5 block text-sm text-slate-300">
            E-mail <span className="text-red-400">*</span>
          </label>
          <input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            autoComplete="email" className={field} />
        </div>
        <div>
          <label htmlFor="message" className="mb-1.5 block text-sm text-slate-300">
            Mensagem <span className="text-red-400">*</span>
          </label>
          <textarea id="message" required rows={5} value={message}
            onChange={(e) => setMessage(e.target.value)} className={field} />
        </div>

        {/* honeypot: invisível para humanos, atraente para bots */}
        <input type="text" tabIndex={-1} autoComplete="off" aria-hidden="true"
          value={website} onChange={(e) => setWebsite(e.target.value)}
          className="absolute left-[-9999px] h-0 w-0 opacity-0" />

        <button type="submit" disabled={status === 'sending'}
          className="inline-flex items-center justify-center rounded-xl bg-brand-500 px-6 py-3.5 text-base font-semibold text-slate-950 transition-colors hover:bg-brand-400 disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300">
          {status === 'sending' ? 'Enviando…' : 'Enviar mensagem'}
        </button>
      </form>

      <p className="mt-6 text-sm text-slate-500">
        Ou, se preferir: <a href="mailto:scan@klarim.net" className="text-brand-400 hover:text-brand-300">scan@klarim.net</a>
      </p>
    </div>
  );
}
