// KL-89 — largura padrão dos containers das páginas públicas.
//
// Antes cada página definia o seu próprio `max-w` (variando de 2xl a 6xl), o que deixava
// espaços vazios laterais em monitores > 1280px e larguras inconsistentes entre páginas. O
// KL-87 só corrigiu o dashboard admin. Este módulo centraliza o padrão: expande
// progressivamente (2xl no mobile → 5xl no md → 7xl no lg) e centraliza, preenchendo 1440px
// sem gerar scroll horizontal a 375px.
//
// Tailwind v4 escaneia arquivos .js do projeto (ver `components/account/ui.js`), então as
// classes literais declaradas aqui entram no build — mesmo interpoladas via `class={CONTAINER}`.

// Container de CONTEÚDO (listagens, resultado do scan, perfis, planos): expandido.
export const CONTAINER =
  'mx-auto w-full max-w-2xl px-4 md:max-w-5xl md:px-6 lg:max-w-7xl lg:px-8';

// Espaçamento vertical padrão abaixo do header fixo (todas as páginas públicas usam este).
export const PAGE_PADDING = 'pt-28 pb-20 sm:pt-32';

// Pronto para o `<main>` das páginas de conteúdo: container expandido + padding de página.
export const PAGE_CONTAINER = `${CONTAINER} ${PAGE_PADDING}`;

// Formulários (login/cadastro/recuperação/contato) e texto corrido (termos/privacidade/sobre)
// leem MELHOR estreitos — esticar um formulário ou um parágrafo até 1440px piora a leitura, não
// melhora. Ficam centralizados e estreitos DE PROPÓSITO (não é "espaço vazio esquecido").
export const CONTAINER_FORM = 'mx-auto w-full max-w-md px-4 sm:px-6';
export const CONTAINER_PROSE = 'mx-auto w-full max-w-3xl px-4 sm:px-6';

// Prontos para o `<main>` de formulários / texto corrido (container estreito + padding).
export const FORM_CONTAINER = `${CONTAINER_FORM} ${PAGE_PADDING}`;
export const PROSE_CONTAINER = `${CONTAINER_PROSE} ${PAGE_PADDING}`;
