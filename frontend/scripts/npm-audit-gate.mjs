// `npm audit` с пропуском разобранных уязвимостей — та же конвенция, что у pip-audit.
//
// У самого `npm audit` нет флага «пропусти вот эту»: одна уязвимость без патча
// краснит гейт навсегда, а вместе с ним и каждый PR. Поэтому аудит читается в
// JSON, и гейт падает, если в дереве есть хоть одна рекомендация (advisory) не из
// списка ниже. Порог прежний — `info`: считается любая рекомендация любой
// тяжести. Транзитивные записи (`chokidar` → `braces`) своих рекомендаций не
// несут, только ссылки на пакет, поэтому решают одни объекты-рекомендации.
//
// Каждая строка списка — разобранное решение: почему не задевает нас и когда
// убрать. Если рекомендация из списка из дерева пропала, гейт об этом
// предупреждает — строку пора удалить.
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Пакет фронтенда — откуда бы скрипт ни запустили (CI идёт из `frontend`, setup.md — из корня).
const FRONTEND_DIR = fileURLToPath(new URL('..', import.meta.url));

// `under` сужает пропуск до одной ветки дерева: рекомендация пропускается, только
// если КАЖДЫЙ уязвимый узел лежит под этим путём. Тот же пакет где-то ещё — снова
// красный гейт, а не тихий пропуск по тому же GHSA.
const IGNORED = new Map([
  [
    // zod < 4.6.6: память исчерпывается на огромных коллекциях с ограничениями. Сам
    // фронтенд уже на zod 4; остался только zod 3 внутри steiger — линтера архитектуры,
    // который валидирует им СВОЙ конфиг (`steiger.config.ts`); в бандл он не попадает.
    // Патча для zod 3 нет, а steiger (вплоть до 0.8.0) на zod 4 падает
    // (`z.function(...).args is not a function`). Убрать, как только steiger перейдёт на zod 4.
    'GHSA-mrhx-c7xc-m3w5',
    {
      under: 'node_modules/steiger/',
      reason: 'zod 3: build-time only via steiger, validating our own config; no zod 3 patch',
    },
  ],
]);

function runAudit() {
  try {
    return execFileSync('npm', ['audit', '--package-lock-only', '--json'], {
      cwd: FRONTEND_DIR,
      encoding: 'utf8',
      shell: process.platform === 'win32',
    });
  } catch (error) {
    // npm audit exits 1 whenever it finds anything; the report is still on stdout.
    if (typeof error.stdout === 'string' && error.stdout.trim() !== '') return error.stdout;
    throw error;
  }
}

const report = JSON.parse(runAudit());
if (report.error || !report.vulnerabilities) {
  console.error('npm audit did not return a report:', JSON.stringify(report.error ?? report));
  process.exit(1);
}

const found = new Map();
for (const [name, entry] of Object.entries(report.vulnerabilities)) {
  for (const via of entry.via) {
    if (typeof via === 'string') continue;
    const id = /GHSA-[\w-]+/.exec(via.url ?? '')?.[0] ?? String(via.source);
    const hit = found.get(id) ?? { line: '', nodes: [] };
    hit.line = `${name} (${via.severity}): ${via.title} ${via.url ?? ''}`.trim();
    hit.nodes.push(...(entry.nodes ?? []));
    found.set(id, hit);
  }
}

const isIgnored = (id, { nodes }) => {
  const rule = IGNORED.get(id);
  if (!rule) return false;
  return !rule.under || (nodes.length > 0 && nodes.every((node) => node.startsWith(rule.under)));
};

const blocking = [...found].filter(([id, hit]) => !isIgnored(id, hit));
for (const [id, { reason }] of IGNORED) {
  const hit = found.get(id);
  if (hit && isIgnored(id, hit)) console.log(`ignored ${id} — ${reason}`);
  else if (hit) console.log(`${id} is reported outside ${IGNORED.get(id).under}; not ignored`);
  else console.log(`::warning::${id} is no longer reported by npm audit; remove it from IGNORED`);
}
for (const [, { line }] of blocking) console.error(line);
if (blocking.length > 0) {
  console.error(`${blocking.length} unreviewed advisory(ies) — fix or add a reviewed ignore`);
  process.exit(1);
}
console.log('npm audit: no unreviewed advisories');
