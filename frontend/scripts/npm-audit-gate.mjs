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

const IGNORED = new Map([
  [
    // braces <= 3.0.3: стек переполняется на глубоко вложенных шаблонах. Патча нет
    // (последний релиз — 3.0.3, май 2024; micromatch/braces#70). С переходом на
    // Tailwind 4 его тянет только steiger (micromatch, и через globby → fast-glob тот
    // же micromatch) — линтер архитектуры, который кормит его НАШИМИ шаблонами из
    // `steiger.config.ts` и путями `src/`; в бандл он не попадает. Убрать, как только
    // выйдет исправленный braces или steiger от micromatch откажется.
    'GHSA-vfj7-8cjw-p6xm',
    'braces: build-time only via steiger, fed our own glob patterns; no patched release',
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
    found.set(id, `${name} (${via.severity}): ${via.title} ${via.url ?? ''}`.trim());
  }
}

const blocking = [...found].filter(([id]) => !IGNORED.has(id));
for (const [id, reason] of IGNORED) {
  if (found.has(id)) console.log(`ignored ${id} — ${reason}`);
  else console.log(`::warning::${id} is no longer reported by npm audit; remove it from IGNORED`);
}
for (const [, line] of blocking) console.error(line);
if (blocking.length > 0) {
  console.error(`${blocking.length} unreviewed advisory(ies) — fix or add a reviewed ignore`);
  process.exit(1);
}
console.log('npm audit: no unreviewed advisories');
