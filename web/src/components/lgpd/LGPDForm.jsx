import { useEffect, useState } from 'react';
import { apiPost } from '../../lib/api.js';
import { field, btn, card, label, errorBox, okBox } from '../account/ui.js';
import { LGPD_TYPES, tipoFromParam, validateLgpdForm } from '../../lib/lgpd.js';
import { maskCPF, isValidCPF } from '../../lib/gate/ux.js';

// KL-161 — formulário de exercício de direitos do titular (DSAR). Público (sem conta). Lê o
// `?tipo=` da URL (o link "Remover meus dados" do perfil manda `?tipo=exclusao`) e pré-seleciona o
// tipo. CPF é OPCIONAL (recomendado): se preenchido e inválido, avisa mas NÃO bloqueia o envio.
// Validação client-side em `lib/lgpd.js` (o backend revalida). Envia a POST /api/lgpd/request.
export default function LGPDForm() {
  const [type, setType] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [cpf, setCpf] = useState('');
  const [description, setDescription] = useState('');
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState('');
  const [done, setDone] = useState(null);   // { id }

  // Pré-seleção do tipo pelo `?tipo=` (ex.: exclusao vindo do perfil público).
  useEffect(() => {
    try {
      const t = tipoFromParam(new URLSearchParams(window.location.search).get('tipo'));
      if (t) setType(t);
    } catch { /* sem query → placeholder */ }
  }, []);

  const cpfInvalid = cpf.trim() !== '' && !isValidCPF(cpf);

  async function submit(e) {
    e.preventDefault();
    setSendError('');
    const { ok, errors: errs } = validateLgpdForm({ type, name, email, description });
    setErrors(errs);
    if (!ok) return;
    setBusy(true);
    const { ok: sent, status, data, error } = await apiPost('/lgpd/request', {
      type, name: name.trim(), email: email.trim(),
      cpf: cpf.trim() || undefined, description: description.trim(),
    });
    setBusy(false);
    if (sent) { setDone({ id: data.id }); return; }
    if (status === 429) return setSendError('Você já enviou várias solicitações hoje. Tente novamente amanhã.');
    setSendError(error || 'Não foi possível enviar a solicitação. Tente novamente.');
  }

  if (done) {
    return (
      <div className={card} role="status">
        <h2 className="text-xl font-bold text-white">✅ Solicitação enviada</h2>
        <p className="mt-3 text-slate-300">
          Você receberá uma confirmação por e-mail. <strong className="text-white">Prazo de
          resposta: até 15 dias úteis</strong>, conforme a LGPD e as diretrizes da ANPD.
        </p>
        {done.id && (
          <p className="mt-3 text-sm text-slate-400">
            Protocolo: <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-slate-200">{done.id}</code>
          </p>
        )}
        <a href="/" className="mt-6 inline-block text-sm text-brand-400 hover:text-brand-300">← Voltar ao início</a>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className={card} noValidate>
      {sendError && <p className={errorBox}>{sendError}</p>}

      <div className="mb-4">
        <label htmlFor="lgpd-type" className={label}>Tipo de solicitação *</label>
        <select id="lgpd-type" value={type} onChange={(e) => setType(e.target.value)} className={field}>
          <option value="">Selecione…</option>
          {LGPD_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        {errors.type && <p className="mt-1 text-sm text-red-400">{errors.type}</p>}
      </div>

      <div className="mb-4">
        <label htmlFor="lgpd-name" className={label}>Nome completo *</label>
        <input id="lgpd-name" type="text" value={name} onChange={(e) => setName(e.target.value)}
          autoComplete="name" className={field} placeholder="Seu nome" />
        {errors.name && <p className="mt-1 text-sm text-red-400">{errors.name}</p>}
      </div>

      <div className="mb-4">
        <label htmlFor="lgpd-email" className={label}>E-mail *</label>
        <input id="lgpd-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          autoComplete="email" className={field} placeholder="voce@exemplo.com.br" />
        {errors.email && <p className="mt-1 text-sm text-red-400">{errors.email}</p>}
      </div>

      <div className="mb-4">
        <label htmlFor="lgpd-cpf" className={label}>CPF <span className="text-slate-500">(opcional, recomendado)</span></label>
        <input id="lgpd-cpf" inputMode="numeric" value={cpf} onChange={(e) => setCpf(maskCPF(e.target.value))}
          className={field} placeholder="000.000.000-00" />
        {cpfInvalid && <p className="mt-1 text-sm text-amber-400">CPF parece inválido — confira os dígitos. Você ainda pode enviar sem o CPF.</p>}
        <p className="mt-1 text-xs text-slate-500">Ajuda a localizar seus dados com mais precisão.</p>
      </div>

      <div className="mb-6">
        <label htmlFor="lgpd-desc" className={label}>Descrição *</label>
        <textarea id="lgpd-desc" value={description} onChange={(e) => setDescription(e.target.value)}
          rows={4} className={field} placeholder="Descreva sua solicitação." />
        {errors.description && <p className="mt-1 text-sm text-red-400">{errors.description}</p>}
      </div>

      <button type="submit" disabled={busy} className={btn}>{busy ? 'Enviando…' : 'Enviar solicitação'}</button>
      <p className="mt-4 text-xs text-slate-500">
        Prazo de resposta: até 15 dias úteis. Também atendemos por
        <a href="mailto:privacidade@klarim.net" className="ml-1 text-brand-400 hover:text-brand-300">privacidade@klarim.net</a>.
      </p>
    </form>
  );
}
