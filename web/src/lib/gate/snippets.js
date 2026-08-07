// KL-152 P1 — helpers PUROS (testáveis) do Security Gate: snippets de CI/CD por plataforma,
// instruções de secret e progresso do plano. Sem DOM/React aqui → cobertos por node --test.
//
// A API key NUNCA é embutida no YAML: o snippet referencia o SECRET do CI (${{ secrets.KLARIM_KEY }}
// no GitHub, $KLARIM_KEY nos demais). A key crua só aparece no passo "adicione o secret" do wizard.
// Usamos o cliente httpx inline (python -c) — funciona em qualquer runner SEM baixar arquivo do repo.

export const TOTAL_CHECKS = 18;                 // security_gate.engine._DEFAULT_ORDER (KL-149)
export const DEFAULT_URL = 'https://meuapp.com.br';
export const SECRET_NAME = 'KLARIM_KEY';

// Referência ao secret por plataforma (string simples — '${' aqui NÃO é interpolação JS).
const SECRET_REF = {
  github: '${{ secrets.KLARIM_KEY }}',
  gitlab: '$KLARIM_KEY',
  bitbucket: '$KLARIM_KEY',
  jenkins: '$KLARIM_KEY',
  manual: '$KLARIM_KEY',
  curl: '$KLARIM_KEY',
};

// Plataformas oferecidas na aba de integração do dashboard vs. no wizard de onboarding.
export const DASHBOARD_PLATFORMS = [
  { id: 'github', label: 'GitHub Actions' },
  { id: 'gitlab', label: 'GitLab CI' },
  { id: 'bitbucket', label: 'Bitbucket' },
  { id: 'curl', label: 'curl' },
];
export const ONBOARDING_PLATFORMS = [
  { id: 'github', label: 'GitHub Actions' },
  { id: 'gitlab', label: 'GitLab CI' },
  { id: 'bitbucket', label: 'Bitbucket' },
  { id: 'jenkins', label: 'Jenkins' },
  { id: 'manual', label: 'Manual/Terminal' },
];

export function secretRef(platform) {
  return SECRET_REF[platform] || SECRET_REF.curl;
}

function pyBody(url, ref, indent) {
  // Corpo httpx inline reusado pelos runners que rodam Python.
  const pad = ' '.repeat(indent);
  return [
    `${pad}pip install httpx`,
    `${pad}python -c "`,
    `${pad}import httpx, sys, json`,
    `${pad}r = httpx.post('https://klarim.net/api/gate/scan',`,
    `${pad}    headers={'X-API-Key': '${ref}'},`,
    `${pad}    json={'url': '${url}', 'fail_on': 'critical'}, timeout=120)`,
    `${pad}d = r.json(); print(json.dumps(d, indent=2))`,
    `${pad}sys.exit(0 if d['passed'] else 1)"`,
  ].join('\n');
}

// Snippet pronto p/ colar (URL do projeto pré-preenchida, secret referenciado — zero edição).
export function buildSnippet(platform, url = DEFAULT_URL) {
  const u = (url || DEFAULT_URL).trim() || DEFAULT_URL;
  const ref = secretRef(platform);
  switch (platform) {
    case 'github':
      return [
        'security-gate:',
        '  needs: deploy',
        '  runs-on: ubuntu-latest',
        '  steps:',
        '    - name: Klarim Security Gate',
        '      run: |',
        pyBody(u, ref, 8),
      ].join('\n');
    case 'gitlab':
      return [
        'security_gate:',
        '  stage: test',
        '  image: python:3.12-slim',
        '  script:',
        pyBody(u, ref, 4).split('\n').map((l) => `    - ${l.trim()}`).join('\n'),
      ].join('\n');
    case 'bitbucket':
      return [
        'pipelines:',
        '  default:',
        '    - step:',
        '        name: Klarim Security Gate',
        '        image: python:3.12-slim',
        '        script:',
        pyBody(u, ref, 8).split('\n').map((l) => `          - ${l.trim()}`).join('\n'),
      ].join('\n');
    case 'jenkins':
      return [
        "stage('Klarim Security Gate') {",
        '  steps {',
        "    sh '''",
        pyBody(u, ref, 6),
        "    '''",
        '  }',
        '}',
      ].join('\n');
    case 'manual':
    case 'curl':
    default:
      return [
        `curl -s https://klarim.net/api/gate/scan \\`,
        `  -H "X-API-Key: ${ref}" -H "Content-Type: application/json" \\`,
        `  -d '{"url":"${u}","fail_on":"critical"}' | jq .`,
      ].join('\n');
  }
}

// Passo "adicione o secret" do wizard (instrução específica por plataforma).
export function secretSteps(platform) {
  switch (platform) {
    case 'github':
      return { where: 'Settings → Secrets and variables → Actions → New repository secret',
               name: SECRET_NAME, flags: [] };
    case 'gitlab':
      return { where: 'Settings → CI/CD → Variables → Add variable',
               name: SECRET_NAME, flags: ['Protected', 'Masked'] };
    case 'bitbucket':
      return { where: 'Repository settings → Pipelines → Repository variables',
               name: SECRET_NAME, flags: ['Secured'] };
    case 'jenkins':
      return { where: 'Manage Jenkins → Credentials → Add (Secret text)',
               name: SECRET_NAME, flags: [] };
    case 'manual':
    default:
      return { where: 'No terminal, exporte a variável antes de rodar', name: SECRET_NAME,
               flags: [], exportLine: `export ${SECRET_NAME}=` };
  }
}

// ---- Progresso do plano (badge + barra) ---- //
// Contagens espelham o seed dos planos (landing). É só um HINT visual — o enforcement é server-side.
const PLAN_CHECKS = { free: 4, pro: 9, team: 18, enterprise: 18 };
const NEXT_PLAN = { free: 'pro', pro: 'team' };
const PLAN_LABEL = { free: 'Free', pro: 'Pro', team: 'Team', enterprise: 'Enterprise' };

export function planProgress(slug, count, total = TOTAL_CHECKS) {
  const c = Math.max(0, Math.min(Number(count) || 0, total));
  const nextSlug = NEXT_PLAN[slug] || null;
  const next = nextSlug
    ? { slug: nextSlug, label: PLAN_LABEL[nextSlug], checks: PLAN_CHECKS[nextSlug] }
    : null;
  return { count: c, total, pct: total ? Math.round((c / total) * 100) : 0, next };
}
