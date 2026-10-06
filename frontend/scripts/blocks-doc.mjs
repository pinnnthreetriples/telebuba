// Собирает `docs/blocks.html` — вторую вкладку дизайн-системы: блоки, которые повторяются
// на экранах (шапка карточки, строка настройки, плитка числа…), каждый одним компонентом.
//
// Разметки руками здесь нет. Образцы описаны в `catalog/blocks/Blocks.tsx` настоящими
// компонентами, Vite загружает их в Node (`ssrLoadModule` с алиасами проекта), а
// `react-dom/server` превращает в HTML. CSS — тот же Tailwind с тем же конфигом и тем же
// `src/app/styles/index.css`, собранный только по этой разметке. Поменяли компонент,
// токен или рецепт — страница разошлась, и `ds:doc:check` падает, пока её не соберут.
//
// «Где используется» тоже не написано руками: генератор ищет `<Имя` по `src` и называет
// слайсы, в которых блок стоит.
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { transform } from 'esbuild';
import postcss from 'postcss';
import tailwind from 'tailwindcss';
import loadConfig from 'tailwindcss/loadConfig.js';
import { createServer } from 'vite';

import { tokens } from './configScales.mjs';
import { esc, rootVars, TOP_CSS, topBar } from './design-md-page.mjs';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const SRC = join(ROOT, 'src');
const HTML_PATH = join(ROOT, 'docs', 'blocks.html');
const ENTRY = '/catalog/blocks/render.tsx';

/* ── Образцы: настоящие компоненты, отрендеренные в Node ──────────────────── */

async function renderBlocks() {
  const server = await createServer({
    root: ROOT,
    configFile: join(ROOT, 'vite.config.ts'),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
    logLevel: 'error',
    optimizeDeps: { noDiscovery: true, include: [] },
    // Свой кэш, не общий `node_modules/.vite`. Иначе этот сервер оставлял там
    // урезанный набор собранных зависимостей, сервер каталога в e2e подхватывал его
    // как готовый, на лету дособирал остальное и перезагружал страницу посреди
    // теста («Execution context was destroyed» в CI, где `ds:doc:check` идёт раньше).
    cacheDir: join(ROOT, 'node_modules', '.vite-blocks-doc'),
  });
  try {
    const mod = await server.ssrLoadModule(ENTRY);
    return mod.renderBlocks();
  } finally {
    await server.close();
  }
}

// Только классы, которые носит разметка страницы, — и весь `index.css` проекта, кроме
// шрифтов: их страница берёт сама, как и `design-md.html`.
async function compileCss(html) {
  const config = loadConfig(join(ROOT, 'tailwind.config.ts'));
  const source = readFileSync(join(SRC, 'app', 'styles', 'index.css'), 'utf8').replace(
    /^@import .*$/gm,
    '',
  );
  const out = await postcss([
    tailwind({ ...config, content: [{ raw: html, extension: 'html' }] }),
  ]).process(source, { from: undefined });
  const min = await transform(out.css, { loader: 'css', minify: true });
  return min.code.trim();
}

/* ── Где блок стоит ───────────────────────────────────────────────────────── */

function sourceFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...sourceFiles(path));
    else if (/\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name)) out.push(path);
  }
  return out.sort();
}

const FILES = sourceFiles(SRC).map((path) => ({
  rel: relative(SRC, path).split(sep).join('/'),
  text: readFileSync(path, 'utf8'),
}));

function definition(name) {
  const found = FILES.filter((f) => new RegExp(`export function ${name}\\b`).test(f.text));
  if (found.length !== 1) {
    throw new Error(`blocks: компонент ${name} объявлен ${String(found.length)} раз, а не один`);
  }
  return found[0].rel;
}

// Слайс — первые два сегмента пути: `pages/neurocomment`, `widgets/account-edit`.
function usages(name, home) {
  const tag = new RegExp(`<${name}[\\s>/]`, 'g');
  const slices = new Map();
  let total = 0;
  for (const file of FILES) {
    if (file.rel === home) continue;
    const hits = file.text.match(tag)?.length ?? 0;
    if (hits === 0) continue;
    total += hits;
    const slice = file.rel.split('/').slice(0, 2).join('/');
    slices.set(slice, (slices.get(slice) ?? 0) + hits);
  }
  return { total, slices: [...slices.keys()].sort() };
}

function places(n) {
  const form = new Intl.PluralRules('ru').select(n);
  return `${String(n)} ${{ one: 'место', few: 'места' }[form] ?? 'мест'}`;
}

/* ── Страница ─────────────────────────────────────────────────────────────── */

const CSS = `
body{margin:0;background:var(--canvas);color:var(--content-primary);font-family:var(--sans);-webkit-font-smoothing:antialiased}
:focus-visible{outline:2px solid var(--action-primary);outline-offset:2px}
.wrap{max-width:1200px;margin:0 auto;padding:var(--s-8) var(--s-6) 96px;display:flex;flex-direction:column;gap:var(--s-6)}
.bk-head{display:flex;align-items:baseline;gap:var(--s-4);flex-wrap:wrap}
.bk-head h1{margin:0;font-size:var(--fs-h1);font-weight:700;letter-spacing:-.02em}
.bk-meta{color:var(--content-subtle);opacity:.8;font-size:var(--fs-small)}
.fs{background:var(--surface-card);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--s-6);scroll-margin-top:68px;min-width:0}
.fs-head{display:flex;align-items:baseline;gap:var(--s-3);flex-wrap:wrap;margin-bottom:var(--s-4)}
.fs-head h2{margin:0;font:500 15px/1.4 var(--mono);letter-spacing:0;color:var(--content-primary)}
.bk-src{font:400 12px/1.4 var(--mono);color:var(--content-subtle);opacity:.8}
.bk-grid{display:grid;gap:var(--s-3);grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr))}
.bk-var{display:flex;flex-direction:column;gap:var(--s-1);min-width:0}
.bk-stage{padding:var(--s-4);border-radius:var(--r-sm);background:var(--canvas);min-width:0;overflow-x:auto}
.bk-label{padding:0 var(--s-1);font-size:12px;line-height:1.4;color:var(--content-subtle);opacity:.8}
.bk-used{margin-top:var(--s-4);display:flex;flex-wrap:wrap;gap:var(--s-1) var(--s-3);font-size:12px;line-height:1.4;color:var(--content-subtle)}
.bk-used span{font-family:var(--mono);opacity:.85}
@media (max-width:720px){
  .wrap{padding:var(--s-6) var(--s-4) 64px}
  .fs{padding:var(--s-4)}
  .bk-stage{padding:var(--s-3)}
}
`;

function blockSection(block) {
  const home = definition(block.name);
  const used = usages(block.name, home);
  // Блок библиотеки (`library: true` в `catalog/blocks/library.tsx`) законно стоит нигде:
  // он пришёл раньше своего экрана. Пометка не может пережить первое место вызова — иначе
  // она станет второй, ручной правдой о том, где блок стоит.
  if (block.library && used.total > 0) {
    throw new Error(`blocks: ${block.name} уже стоит в src — снимите пометку library`);
  }
  if (!block.library && used.total === 0) throw new Error(`blocks: ${block.name} нигде не стоит`);
  const variants = block.variants
    .map(
      (v) =>
        `<div class="bk-var"><div class="bk-stage">${v.html}</div>${v.label ? `<div class="bk-label">${esc(v.label)}</div>` : ''}</div>`,
    )
    .join('\n');
  const where = used.slices.map((s) => `<span>${esc(s)}</span>`).join('');
  return `<section class="fs" id="${esc(block.id)}" aria-labelledby="${esc(block.id)}-h">
<div class="fs-head"><h2 id="${esc(block.id)}-h">${esc(block.name)}</h2><span class="bk-src">src/${esc(home)}</span></div>
<div class="bk-grid">
${variants}
</div>
<div class="bk-used">${block.library ? 'Библиотека · пока не стоит ни на одном экране' : places(used.total) + where}</div>
</section>`;
}

async function renderPage() {
  const blocks = await renderBlocks();
  const sections = blocks.map(blockSection);
  const css = await compileCss(sections.join('\n'));
  const nav = blocks.map((b) => `<a href="#${esc(b.id)}">${esc(b.name)}</a>`).join('');
  return `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telebuba Блоки</title>
<!-- Собрано scripts/blocks-doc.mjs из catalog/blocks и src/shared/ui. Не править руками: npm run ds:doc -->
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
${css}
:root{
${rootVars(tokens)}
}
${CSS.trim()}
${TOP_CSS.trim()}
</style>
</head>
<body>
${topBar('blocks', nav)}
<main class="wrap">
<div class="bk-head"><h1>Блоки</h1><span class="bk-meta">${String(blocks.length)} блоков · src/shared/ui</span></div>
${sections.join('\n')}
</main>
</body>
</html>
`;
}

/* ── Запуск ───────────────────────────────────────────────────────────────── */

async function main() {
  const check = process.argv.includes('--check');
  const wanted = await renderPage();
  let actual = '';
  try {
    actual = readFileSync(HTML_PATH, 'utf8').replace(/\r\n/g, '\n');
  } catch {
    // Нет файла — разошёлся так же, как устаревший.
  }
  if (actual === wanted) {
    process.stdout.write('blocks.html: соответствует компонентам\n');
    return 0;
  }
  if (check) {
    process.stderr.write('blocks.html разошёлся с компонентами. Собрать заново: npm run ds:doc\n');
    return 1;
  }
  writeFileSync(HTML_PATH, wanted, 'utf8');
  process.stdout.write('blocks.html: обновлён из компонентов\n');
  return 0;
}

process.exit(await main());
