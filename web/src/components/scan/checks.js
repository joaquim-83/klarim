// Agrupamento dos 48 checks em 6 camadas, para o resultado e o progresso (KL-51 f2).
// A ordem aqui é a ordem de exibição das categorias.
export const CATEGORIES = [
  'Transporte & TLS',
  'Headers de segurança',
  'Supply chain',
  'DNS & E-mail',
  'Conteúdo',
  'OSINT & Reputação',
];

const BY_CATEGORY = {
  'Transporte & TLS': [1, 2, 3, 4, 41, 42, 43, 44],
  'Headers de segurança': [5, 6, 7, 8, 17, 18, 31, 32, 33, 34, 35, 36],
  'Supply chain': [13, 14, 15, 30],
  'DNS & E-mail': [21, 22, 23, 37, 38, 39, 40],
  'Conteúdo': [9, 10, 11, 12, 24, 25, 45, 46, 47, 48],
  'OSINT & Reputação': [16, 19, 20, 26, 27, 28, 29],
};

const NUM_TO_CATEGORY = (() => {
  const map = {};
  for (const cat of CATEGORIES) for (const n of BY_CATEGORY[cat]) map[n] = cat;
  return map;
})();

export function categoryOf(checkId) {
  const m = /check_(\d+)_/.exec(checkId || '');
  const n = m ? parseInt(m[1], 10) : 0;
  return NUM_TO_CATEGORY[n] || 'Outros';
}

// Agrupa a lista de checks por categoria (mantém a ordem de CATEGORIES).
export function groupByCategory(checks) {
  const groups = new Map(CATEGORIES.map((c) => [c, []]));
  for (const c of checks) {
    const cat = categoryOf(c.check_id);
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat).push(c);
  }
  return [...groups.entries()].filter(([, list]) => list.length > 0);
}
