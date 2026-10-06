// HTML-страница DESIGN.md в духе ui-skills.com: одна колонка, разделы карточками на всю
// ширину, внутри — аккуратные блоки в сетке (цвета группами, роли текста образцом «Aa»,
// ступени, формы, движение на живых примерах, компоненты), а сам DESIGN.md — последним
// блоком внизу с кнопками «Скопировать» и «Скачать».
//
// Страница красится собственными токенами системы: `:root` ниже собран из них же, и
// перекраска палитры перекрашивает и документ. Раскладка самой страницы (ширина, сетка
// блоков) — геометрия документа, а не продукта, и поэтому записана числами. Кадры
// анимаций повторяют кейфреймы `src/app/styles/index.css`, а длительности и кривые
// приходят из токенов.
//
// Модуль ничего не читает сам: спецификацию собирает `design-md.mjs`.

export const esc = (s) =>
  String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

function flatColors(scale) {
  const out = {};
  for (const [name, value] of Object.entries(scale)) {
    if (typeof value === 'string') out[name] = value;
    else
      for (const [rung, v] of Object.entries(value))
        out[rung === 'DEFAULT' ? name : `${name}-${rung}`] = v;
  }
  return out;
}

function resolver(tokens) {
  const table = {
    colors: flatColors(tokens.flatColors),
    fontSize: tokens.fontSize,
    rounded: tokens.radius,
    height: tokens.height,
    spacing: tokens.rhythm,
    shadows: tokens.shadow,
  };
  return (value) => {
    const m = /^\{(\w+)\.(.+)\}$/.exec(String(value));
    if (m === null) return String(value);
    const found = table[m[1]]?.[m[2]];
    if (found === undefined) throw new Error(`design-md: ссылка ${value} никуда не ведёт`);
    return String(found);
  };
}

/* ── Верхняя полоса: общая у двух страниц ─────────────────────────────────── */

// Две вкладки — эта страница и «Блоки» (`blocks.html`, собирает `blocks-doc.mjs`). Ссылки
// относительные: обе страницы лежат в `docs/` рядом и открываются файлом.
const TABS = [
  ['design', 'design-md.html', 'Дизайн-система'],
  ['blocks', 'blocks.html', 'Блоки'],
];

export const TOP_CSS = `
.top{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--canvas) 86%,transparent);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.top-in{max-width:1200px;margin:0 auto;padding:0 var(--s-6);height:52px;display:flex;align-items:center;gap:var(--s-3)}
.logo{width:22px;height:22px;flex:none;border-radius:var(--r-sm);background:var(--content-primary);display:grid;place-items:center}
.logo i{width:7px;height:7px;border-radius:50%;background:var(--action-primary)}
.crumb{display:flex;gap:var(--s-2);align-items:center;color:var(--content-subtle)}
.crumb b{color:var(--content-primary);font-weight:600}
.tabs{display:flex;gap:2px;padding:2px;border-radius:var(--r-full);background:var(--line)}
.tabs a{padding:var(--s-1) var(--s-3);border-radius:var(--r-full);font-size:13px;font-weight:500;line-height:20px;color:var(--content-subtle);white-space:nowrap;text-decoration:none}
.tabs a:hover{color:var(--content-primary)}
.tabs a[aria-current=page]{background:var(--surface-card);color:var(--content-primary)}
.top nav{margin-left:auto;display:flex;gap:var(--s-4);font-size:13px;min-width:0;overflow:hidden}
.top nav a{color:var(--content-subtle);white-space:nowrap;text-decoration:none}
.top nav a:hover{color:var(--content-primary)}
@media (max-width:720px){
  .top-in{padding:0 var(--s-4)}
  .top nav{display:none}
}
`;

export function topBar(active, nav) {
  const tabs = TABS.map(
    ([id, href, label]) =>
      `<a href="${href}"${id === active ? ' aria-current="page"' : ''}>${esc(label)}</a>`,
  ).join('');
  return `<header class="top"><div class="top-in">
<span class="logo"><i></i></span>
<span class="crumb"><b>Telebuba</b></span>
<div class="tabs" role="navigation" aria-label="Страницы">${tabs}</div>
<nav>${nav}</nav>
</div></header>`;
}

/* ── Строка значения ──────────────────────────────────────────────────────── */

const HASH =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path stroke-linecap="round" d="M5.25 8.25h15m-16.5 7.5h15m-1.8-13.5-3.9 19.5m-2.1-19.5-3.9 19.5"/></svg>';

function valueRow({ name, value, swatch, mark, title }) {
  const lead = swatch
    ? `<span class="sw" style="background:${esc(swatch)}"></span>`
    : `<span class="mk">${mark ?? HASH}</span>`;
  const hint = title ? ` title="${esc(title)}"` : '';
  return `<button type="button" class="tv" data-copy="${esc(value)}"${hint} aria-label="Скопировать ${esc(name)}: ${esc(value)}">${lead}<span class="nm">${esc(name)}</span><span class="vl">${esc(value)}</span><span class="tip">Скопировать</span></button>`;
}

// Только заголовок: подписей на странице нет — имя ступени и её значение говорят сами.
function section(id, title, body) {
  return `<section class="fs" id="${id}" aria-labelledby="${id}-h">
<div class="fs-head"><h2 id="${id}-h">${esc(title)}</h2></div>
${body}
</section>`;
}

/* ── Разделы ──────────────────────────────────────────────────────────────── */

function howToSection(spec) {
  const rows = spec.howTo.map(([what, where]) => valueRow({ name: what, value: where })).join('\n');
  return section('how', 'Где менять', `<div class="grid g2 rows">${rows}</div>`);
}

function colorsSection(spec) {
  const blocks = spec.colors.map((group) => {
    const rows = group.items
      .map((c) => `<div>${valueRow({ name: c.id, value: c.value, swatch: c.value })}</div>`)
      .join('');
    const strip = group.items
      .map((c) => `<span style="background:${esc(c.value)}" title="${esc(c.id)}"></span>`)
      .join('');
    return `<div class="blk"><div class="strip">${strip}</div><h3>${esc(group.title)}</h3><div class="rows">${rows}</div></div>`;
  });
  return section('colors', 'Цвета', `<div class="cols">${blocks.join('\n')}</div>`);
}

// Шкала, как её рисуют в типографских таблицах: строка — один стиль, от крупного к
// мелкому, образец набран самим стилем. Справа — роли, которые его носят (клик копирует
// класс), и параметры, подписанные так же, как у Firecrawl и в YAML DESIGN.md:
// `fontSize`, `fontWeight`, `lineHeight`, `letterSpacing`. Роли, отличающиеся
// только серым, стоят в одной строке: строк столько, сколько ступеней на самом деле.
const SAMPLE = 'Съешь же ещё этих мягких французских булок';

function typographySection(spec) {
  const rows = spec.typeStyles.map(({ style, roles }) => {
    const css = [
      'font-family:var(--sans)',
      `font-size:${style.fontSize}`,
      `font-weight:${style.fontWeight}`,
      `letter-spacing:${style.letterSpacing}`,
      `line-height:${style.lineHeight}`,
      style.textTransform ? `text-transform:${style.textTransform}` : '',
    ]
      .filter(Boolean)
      .join(';');
    const names = roles
      .map(
        (r) =>
          `<button type="button" class="sname" data-copy="type-${esc(r.name)}" title="Скопировать type-${esc(r.name)}">${esc(r.name)}</button>`,
      )
      .join('');
    const params = [
      ['fontSize', style.fontSize],
      ['fontWeight', style.fontWeight],
      ['lineHeight', style.lineHeight],
      ['letterSpacing', style.letterSpacing],
    ]
      .map(([k, v]) => `<span class="sk"><i>#</i>${k}</span><span class="sv">${esc(v)}</span>`)
      .join('');
    return `<div class="srow"><span class="sample" style="${css}">${SAMPLE}</span><span class="snames">${names}</span><span class="sval">${params}</span></div>`;
  });
  const fonts = Object.entries(spec.fontFamily)
    .map(
      ([k, v]) =>
        `<div class="blk font"><span class="aa" style="font-family:var(--${k})">Aa</span><div class="rows">${valueRow({ name: k, value: v, mark: 'T' })}</div></div>`,
    )
    .join('');
  return section(
    'typography',
    'Типографика',
    `<div class="grid g2">${fonts}</div><div class="scale">${rows.join('\n')}</div>`,
  );
}

function spacingSection(spec) {
  const blocks = spec.spacing.map((s) => {
    const px = parseFloat(s.value);
    return `<div class="blk step"><div class="ruler"><span style="width:${px}px"></span></div>${valueRow({ name: s.name, value: s.value })}</div>`;
  });
  return section('spacing', 'Сетка отступов', `<div class="grid g4">${blocks.join('\n')}</div>`);
}

function shapesSection(spec) {
  // Как у Firecrawl: залитая форма без рамки и строка значения под ней.
  const radius = spec.rounded.map(
    (r) =>
      `<div class="item"><div class="shape" style="border-radius:${esc(r.value)}"></div>${valueRow({ name: r.name, value: r.value })}</div>`,
  );
  const shadows = spec.shadows.map(
    (s) =>
      `<div class="blk"><div class="lift" style="box-shadow:${esc(s.value)}"></div>${valueRow({ name: s.name, value: s.value })}</div>`,
  );
  return `${section('rounded', 'Скругления', `<div class="grid g4 radii">${radius.join('\n')}</div>`)}
${section('shadows', 'Тени', `<div class="grid g3">${shadows.join('\n')}</div>`)}`;
}

/* ── Движение: живые примеры ──────────────────────────────────────────────── */

// Что каждая длительность делает в продукте — тем же элементом, который её носит.
const MOTION_DEMO = {
  state: {
    stage:
      '<button type="button" class="btn c-button-secondary">Наведите</button><button type="button" class="btn c-button-primary">Нажмите</button>',
  },
  enter: {
    replay: true,
    stage: '<div class="m-toast run" data-anim>Прокси сохранён</div>',
  },
  swap: {
    replay: true,
    stage:
      '<div class="m-swap run" data-anim><span class="m-val">30 сек</span><span class="m-ok" aria-hidden="true">✓</span></div>',
  },
  reveal: {
    stage:
      '<div class="m-coll"><button type="button" class="m-coll-h" aria-expanded="true" data-toggle><span>Каналы кампании</span><span class="m-chev" aria-hidden="true">▾</span></button><div class="m-coll-b"><div>@crypto_news</div><div>@defi_daily</div><div>@ton_chat</div></div></div>',
  },
  spin: {
    stage:
      '<span class="m-spin" aria-hidden="true"></span><button type="button" class="btn c-button-primary" aria-busy="true"><span class="m-spin on" aria-hidden="true"></span>Сохраняю…</button>',
  },
  roll: {
    replay: true,
    stage:
      '<div class="m-roll run" data-anim><span class="m-num">72%</span><span class="m-track"><span></span></span></div>',
  },
  pulse: {
    stage:
      '<span class="m-live" aria-hidden="true"></span><span class="m-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="m-load">Загрузка диалогов</span>',
  },
  stagger: {
    replay: true,
    stage:
      '<div class="m-stag run" data-anim><span>Старое: 25 сек</span><span>Новое: 30 сек</span></div>',
  },
};

// `cubic-bezier(a,b,c,d)` → кривая на квадрате 100×100. `linear` — диагональ.
function curvePath(value) {
  const m = /cubic-bezier\(([^)]+)\)/.exec(value);
  const [x1, y1, x2, y2] = m === null ? [0, 0, 1, 1] : m[1].split(',').map(Number);
  const pt = (x, y) => `${(x * 100).toFixed(0)},${(100 - y * 100).toFixed(0)}`;
  return `M0,100 C${pt(x1, y1)} ${pt(x2, y2)} 100,0`;
}

function motionSection(spec) {
  const durations = spec.duration.map((d) => {
    const demo = MOTION_DEMO[d.name];
    const replay = demo?.replay
      ? '<button type="button" class="replay" data-replay>Повторить</button>'
      : '';
    return `<div class="blk motion"><div class="stage" data-motion="${esc(d.name)}">${demo?.stage ?? ''}${replay}</div>${valueRow({ name: d.name, value: d.value })}</div>`;
  });
  const easings = spec.easing.map(
    (e) =>
      `<div class="blk motion"><div class="curve"><svg viewBox="-6 -40 112 180" aria-hidden="true"><path class="axis" d="M0,100 H100 M0,100 V0"/><path class="bez" d="${curvePath(e.value)}"/></svg><span class="track"><span class="ball" style="animation-timing-function:${esc(e.value)}"></span></span></div>${valueRow({ name: e.name, value: e.value, title: e.value })}</div>`,
  );
  return section(
    'motion',
    'Движение',
    `<h3 class="sub">Длительность</h3><div class="grid g4">${durations.join('\n')}</div><h3 class="sub">Кривые</h3><div class="grid g4">${easings.join('\n')}</div>`,
  );
}

/* ── Компоненты ───────────────────────────────────────────────────────────── */

const CSS_PROP = {
  backgroundColor: 'background',
  textColor: 'color',
  borderColor: 'border-color',
  borderStyle: 'border-style',
  fontSize: 'font-size',
  fontWeight: 'font-weight',
  rounded: 'border-radius',
  height: 'height',
  shadow: 'box-shadow',
};

function componentCss(name, props, resolve) {
  const base = [];
  const hover = [];
  for (const [key, raw] of Object.entries(props)) {
    const isHover = key.startsWith('hover');
    const prop = isHover ? key[5].toLowerCase() + key.slice(6) : key;
    const value = resolve(raw);
    const into = isHover ? hover : base;
    if (prop === 'paddingX') into.push(`padding-left:${value}`, `padding-right:${value}`);
    else if (prop === 'paddingY') into.push(`padding-top:${value}`, `padding-bottom:${value}`);
    else if (prop === 'borderWidth') into.push(`border-width:${value}`, 'border-style:solid');
    else if (CSS_PROP[prop]) into.push(`${CSS_PROP[prop]}:${value}`);
  }
  if (props.borderStyle) base.push(`border-style:${props.borderStyle}`);
  return `.c-${name}{${base.join(';')}}${hover.length ? `.c-${name}:hover{${hover.join(';')}}` : ''}`;
}

function componentSample(c) {
  if (c.group === 'button')
    return `<button type="button" class="btn c-${c.name}">${esc(c.label)}</button>`;
  if (c.group === 'button-size') {
    return `<button type="button" class="btn c-button-secondary c-${c.name}">Размер ${esc(c.name.replace('button-', ''))}</button>`;
  }
  if (c.group === 'input') {
    const flat = c.name === 'input-flat';
    return `<input class="fld c-${c.name}" ${flat ? 'readonly value="api_id: 2040"' : 'placeholder="Название кампании"'} aria-label="${esc(c.name)}">`;
  }
  if (c.group === 'badge')
    return `<span class="bdg c-${c.name}">${esc(c.name.replace('badge-', ''))}</span>`;
  const text =
    c.name === 'inverse'
      ? '<span class="mono">18:42 · комментарий отправлен</span>'
      : '<b>Каналы кампании</b><span>3 канала · 12 аккаунтов</span>';
  return `<div class="srf c-${c.name}">${text}</div>`;
}

function componentsSection(spec, resolve) {
  const groups = [
    ['button', 'Кнопки'],
    ['button-size', 'Размеры кнопок'],
    ['input', 'Поля'],
    ['surface', 'Поверхности'],
    ['badge', 'Плашки'],
  ];
  const body = groups
    .map(([group, title]) => {
      const cards = spec.components
        .filter((c) => c.group === group)
        .map((c) => {
          const rows = Object.entries(c.props)
            .map(([k, v]) => {
              const value = resolve(v);
              return valueRow({
                name: k,
                value: String(v) === value ? value : `${v} ${value}`,
                swatch: /color/i.test(k) ? value : undefined,
              });
            })
            .join('');
          return `<div class="blk comp"><div class="stage">${componentSample(c)}</div><div class="cname"><b>${esc(c.name)}</b></div><details><summary>Значения</summary><div class="rows">${rows}</div></details></div>`;
        })
        .join('\n');
      return `<h3 class="sub">${title}</h3><div class="grid g3">${cards}</div>`;
    })
    .join('\n');
  return section('components', 'Компоненты', body);
}

/* ── DESIGN.md с подсветкой ───────────────────────────────────────────────── */

function highlight(md) {
  let inFront = false;
  let fences = 0;
  return md
    .split('\n')
    .map((line) => {
      if (line === '---' && fences < 2) {
        fences += 1;
        inFront = fences === 1;
        return '<span class="y-c">---</span>';
      }
      const text = esc(line);
      if (inFront) {
        if (/^\s*#/.test(line)) return `<span class="y-c">${text}</span>`;
        return text.replace(/^(\s*)([^:\s][^:]*?):(\s|$)(.*)$/, (_, sp, key, gap, val) => {
          const value = val === '' ? '' : `<span class="y-s">${val}</span>`;
          return `${sp}<span class="y-k">${key}</span>:${gap}${value}`;
        });
      }
      if (/^#{1,6}\s/.test(line)) return `<span class="y-h">${text}</span>`;
      return text.replace(/`([^`]+)`/g, '<span class="y-i">`$1`</span>');
    })
    .join('\n');
}

/* ── Корневые переменные из токенов ───────────────────────────────────────── */

export function rootVars(tokens) {
  const lines = [];
  for (const [id, value] of Object.entries(flatColors(tokens.flatColors))) {
    if (id === 'transparent' || id === 'current') continue;
    lines.push(`  --${id}:${value};`);
  }
  lines.push(`  --sans:${tokens.font.sans.join(',')};`, `  --mono:${tokens.font.mono.join(',')};`);
  for (const [k, v] of Object.entries(tokens.radius)) lines.push(`  --r-${k}:${v};`);
  for (const [k, v] of Object.entries(tokens.rhythm)) lines.push(`  --s-${k}:${v};`);
  for (const [k, v] of Object.entries(tokens.fontSize)) lines.push(`  --fs-${k}:${v};`);
  for (const [k, v] of Object.entries(tokens.duration)) lines.push(`  --d-${k}:${v};`);
  for (const [k, v] of Object.entries(tokens.easing)) lines.push(`  --e-${k}:${v};`);
  lines.push(
    `  --shadow-pop:${tokens.shadow.pop};`,
    `  --shadow-focus:${tokens.shadow.focus};`,
    `  --press:${tokens.pressScale.press};`,
  );
  return lines.join('\n');
}

/* ── Стили страницы ───────────────────────────────────────────────────────── */

const CSS = `
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--canvas);color:var(--content-primary);font:400 var(--fs-body)/1.5 var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--info-strong);text-decoration:none}
code{font-family:var(--mono);font-size:12px;color:var(--content-muted);background:var(--surface);padding:1px 5px;border-radius:var(--r-sm)}
:focus-visible{outline:2px solid var(--action-primary);outline-offset:2px}
.wrap{max-width:1200px;margin:0 auto;padding:var(--s-8) var(--s-6) 96px;display:flex;flex-direction:column;gap:var(--s-6)}
.head{display:flex;gap:var(--s-4);align-items:center}
.head .icon{width:52px;height:52px;flex:none;border-radius:var(--r-lg);background:var(--content-primary);display:grid;place-items:center}
.head .icon i{width:14px;height:14px;border-radius:50%;background:var(--action-primary);box-shadow:0 0 0 6px color-mix(in srgb,var(--action-primary) 25%,transparent)}
.head h1{margin:0 0 2px;font-size:var(--fs-h1);font-weight:700;letter-spacing:-.02em}
.head p{margin:0;color:var(--content-subtle);max-width:640px}
.meta{margin-left:auto;text-align:right;color:var(--content-subtle);opacity:.8;font-size:var(--fs-small);white-space:nowrap}
.preview{border:1px solid var(--line);border-radius:var(--r-lg);background:var(--surface);padding:var(--s-8);display:grid;place-items:center}
.preview .card{width:100%;max-width:520px;background:var(--surface-card);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--s-6);display:flex;flex-direction:column;gap:var(--s-3)}
.preview .row{display:flex;gap:var(--s-2);align-items:center;flex-wrap:wrap}
.preview .eyebrow{font-size:var(--fs-small);font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--content-subtle)}
.preview .title{font-size:var(--fs-h3);font-weight:600}
.preview .cap{font-size:var(--fs-small);color:var(--content-subtle)}
.fs{background:var(--surface-card);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--s-6)}
.fs-head{margin-bottom:var(--s-6)}
.fs-head h2{margin:0;font-size:var(--fs-h3);font-weight:600;letter-spacing:-.01em}
.fs-head p{margin:var(--s-1) 0 0;color:var(--content-subtle);opacity:.85;font-size:13px}
.sub{margin:var(--s-6) 0 var(--s-3);font-size:var(--fs-small);font-weight:500;letter-spacing:.04em;text-transform:uppercase;color:var(--content-subtle);opacity:.75}
.fs-head+.sub{margin-top:0}
.grid{display:grid;gap:var(--s-3)}
.g2{grid-template-columns:repeat(auto-fill,minmax(380px,1fr))}
.g3{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.g4{grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}
.cols{columns:3 300px;column-gap:var(--s-3)}
.cols>.blk{break-inside:avoid;margin-bottom:var(--s-3)}
.blk{border:1px solid var(--canvas);border-radius:var(--r-sm);padding:var(--s-3);display:flex;flex-direction:column;gap:var(--s-1);min-width:0}
.blk h3{margin:var(--s-1) 0 var(--s-1);font-size:13px;font-weight:500;color:var(--content-secondary)}
.rows{display:flex;flex-direction:column;gap:var(--s-1)}
.note{margin:2px 0 var(--s-1) var(--s-2);font-size:12px;line-height:1.4;color:var(--content-subtle);opacity:.7}
.tv{all:unset;box-sizing:border-box;position:relative;display:flex;align-items:center;gap:var(--s-2);width:100%;min-width:0;padding:var(--s-1) var(--s-2);border-radius:var(--r-sm);background:var(--surface);cursor:pointer;transition:background var(--d-state) var(--e-out)}
.tv:hover,.tv:focus-visible{background:var(--canvas)}
.tv:focus-visible{outline:2px solid var(--action-primary);outline-offset:1px}
.sw{width:16px;height:16px;flex:none;border-radius:4px;box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--content-primary) 10%,transparent)}
.mk{width:16px;height:16px;flex:none;border-radius:4px;border:1px solid var(--line);display:inline-flex;align-items:center;justify-content:center;font-size:10px;color:var(--content-subtle)}
.mk svg{width:12px;height:12px}
.nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;color:var(--content-secondary)}
.vl{flex:none;max-width:62%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:400 12px/1.4 var(--mono);color:var(--content-subtle);opacity:.85}
.tip{position:absolute;right:0;bottom:calc(100% + 6px);padding:var(--s-1) var(--s-2);border-radius:var(--r-sm);background:var(--term);color:var(--on-fill);font-size:var(--fs-small);white-space:nowrap;opacity:0;pointer-events:none;transition:opacity var(--d-state);z-index:5}
.tv:hover .tip,.tv:focus-visible .tip,.tv[data-copied] .tip{opacity:1}
.strip{display:flex;height:28px;border-radius:var(--r-sm);overflow:hidden;box-shadow:inset 0 0 0 1px var(--canvas)}
.strip span{flex:1}
.aa{line-height:1}
.scale{margin-top:var(--s-6);border-top:1px dashed var(--line)}
.srow{display:grid;grid-template-columns:minmax(0,1fr) auto 196px;align-items:center;gap:var(--s-6);padding:var(--s-3) 0;border-bottom:1px dashed var(--line)}
.sample{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.3;color:var(--content-primary)}
.snames{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.sname{all:unset;cursor:pointer;font-size:13px;font-weight:500;color:var(--content-secondary);text-align:right}
.sname:hover{color:var(--action-primary)}
.sname:focus-visible{outline:2px solid var(--action-primary);outline-offset:2px}
.sval{display:grid;grid-template-columns:auto auto;justify-content:space-between;column-gap:var(--s-4);row-gap:2px;font-size:12px;line-height:1.5}
.sk{color:var(--content-subtle)}
.sk i{font-style:normal;display:inline-block;width:12px;margin-right:var(--s-1);color:var(--content-subtle);opacity:.6}
.sv{font-family:var(--mono);color:var(--content-secondary);text-align:right}
.font .aa{font-size:56px;font-weight:500;padding:var(--s-2);color:var(--content-primary)}
.step .ruler{height:40px;display:flex;align-items:center;padding:0 var(--s-2)}
.step .ruler span{display:block;height:24px;min-width:1px;background:color-mix(in srgb,var(--action-primary) 18%,transparent);border-left:1px solid var(--action-primary);border-right:1px solid var(--action-primary)}
.shape{height:48px;background:var(--canvas)}
.radii .item{gap:var(--s-2)}
.lift{height:56px;border-radius:var(--r-md);background:var(--surface-card);border:1px solid var(--canvas);margin:var(--s-2) var(--s-2) var(--s-3)}
.stage{position:relative;min-height:96px;display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:var(--s-2);padding:var(--s-3);border-radius:var(--r-sm);background:var(--surface)}
.comp .stage{min-height:84px}
.cname{display:flex;flex-direction:column;gap:2px;padding:var(--s-1) var(--s-1) 0}
.cname b{font:500 12px/1.4 var(--mono);color:var(--content-secondary)}
.cname .note{margin-left:0}
details summary{cursor:pointer;font-size:12px;color:var(--content-subtle);opacity:.8;padding:var(--s-1);list-style:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:'›';display:inline-block;margin-right:6px;transition:transform var(--d-state) var(--e-out)}
details[open] summary::before{transform:rotate(90deg)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:var(--s-1);white-space:nowrap;border:0;background:transparent;font-family:var(--sans);cursor:pointer;transition:background var(--d-state) var(--e-out),border-color var(--d-state) var(--e-out),color var(--d-state) var(--e-out),transform var(--d-state) var(--e-out)}
.btn:active{transform:scale(var(--press))}
.btn:focus-visible{outline:2px solid var(--action-primary);outline-offset:2px}
.fld{width:100%;max-width:260px;font:inherit;color:var(--content-primary);outline:none;transition:border-color var(--d-state) var(--e-out),box-shadow var(--d-state) var(--e-out)}
.fld:focus{border-color:var(--action-primary);box-shadow:var(--shadow-focus)}
.bdg{display:inline-flex;align-items:center;white-space:nowrap}
.srf{width:100%;padding:var(--s-3);display:flex;flex-direction:column;gap:2px;font-size:13px}
.srf span{color:inherit;opacity:.7;font-size:var(--fs-small)}
.mono{font-family:var(--mono);font-size:var(--fs-small)}
.replay{all:unset;position:absolute;right:var(--s-1);bottom:var(--s-1);font-size:11px;color:var(--content-subtle);opacity:.8;padding:2px 6px;border-radius:var(--r-sm);cursor:pointer}
.replay:hover{background:var(--canvas);opacity:1}
.replay:focus-visible{outline:2px solid var(--action-primary)}
@keyframes m-fadeup{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes m-swapin{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
@keyframes m-okpop{0%{transform:scale(.6);opacity:0}60%{transform:scale(1.08)}100%{transform:scale(1);opacity:1}}
@keyframes m-spin{to{transform:rotate(360deg)}}
@keyframes m-roll{from{width:8%}to{width:72%}}
@keyframes m-count{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes m-live{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--action-primary) 55%,transparent);transform:scale(1)}60%{box-shadow:0 0 0 5px transparent;transform:scale(1.18)}100%{box-shadow:0 0 0 0 transparent;transform:scale(1)}}
@keyframes m-dot{0%,100%{opacity:1}50%{opacity:.16}}
@keyframes m-glow{0%,100%{box-shadow:0 0 0 0 transparent}50%{box-shadow:0 0 7px 1px color-mix(in srgb,var(--on-action-track) 90%,transparent)}}
@keyframes m-ball{from{left:0}to{left:calc(100% - 10px)}}
.m-toast{padding:var(--s-2) var(--s-4);border-radius:var(--r-md);background:var(--term);color:var(--on-fill);font-size:13px;box-shadow:var(--shadow-pop)}
.m-toast.run{animation:m-fadeup var(--d-enter) var(--e-out) both}
.m-swap{display:flex;align-items:center;gap:var(--s-2);font-size:var(--fs-h3);font-weight:600}
.m-swap.run .m-val{animation:m-swapin var(--d-swap) var(--e-out) both}
.m-ok{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--success);color:var(--on-fill);font-size:12px}
.m-swap.run .m-ok{animation:m-okpop var(--d-swap) var(--e-out) both}
.m-coll{width:100%;max-width:240px;border:1px solid var(--line);border-radius:var(--r-md);background:var(--surface-card);overflow:hidden}
.m-coll-h{all:unset;box-sizing:border-box;width:100%;display:flex;justify-content:space-between;align-items:center;padding:var(--s-2) var(--s-3);font-size:13px;font-weight:600;cursor:pointer}
.m-coll-h:focus-visible{outline:2px solid var(--action-primary);outline-offset:-2px}
.m-chev{transition:transform var(--d-reveal) var(--e-spring);color:var(--content-subtle)}
.m-coll-h[aria-expanded=false] .m-chev{transform:rotate(-90deg)}
.m-coll-b{display:grid;gap:2px;padding:0 var(--s-3);font-size:12px;color:var(--content-muted);max-height:90px;opacity:1;transition:max-height var(--d-reveal) var(--e-spring),opacity var(--d-enter) var(--e-out),padding var(--d-reveal) var(--e-spring);padding-bottom:var(--s-2)}
.m-coll-h[aria-expanded=false]+.m-coll-b{max-height:0;opacity:0;padding-bottom:0}
.m-spin{display:inline-block;width:20px;height:20px;border-radius:50%;border:2px solid var(--line);border-top-color:var(--action-primary);animation:m-spin var(--d-spin) var(--e-linear) infinite}
.m-spin.on{width:14px;height:14px;border-color:var(--on-action-track);border-top-color:var(--on-fill)}
.m-roll{width:100%;display:flex;flex-direction:column;gap:var(--s-2);align-items:flex-start}
.m-num{font-size:var(--fs-h1);font-weight:700}
.m-roll.run .m-num{animation:m-count var(--d-roll) var(--e-out) both}
.m-track{display:block;width:100%;height:6px;border-radius:var(--r-full);background:var(--canvas);overflow:hidden}
.m-track span{display:block;height:100%;width:72%;border-radius:inherit;background:var(--action-primary)}
.m-roll.run .m-track span{animation:m-roll var(--d-roll) var(--e-out) both}
.m-live{width:9px;height:9px;border-radius:50%;background:var(--action-primary);animation:m-live var(--d-pulse) var(--e-out) infinite}
.m-dots{display:inline-flex;gap:3px;padding:var(--s-2) var(--s-3);border-radius:var(--r-full);background:var(--surface-card);border:1px solid var(--canvas)}
.m-dots i{width:5px;height:5px;border-radius:50%;background:var(--content-subtle);animation:m-dot var(--d-pulse) var(--e-breathe) infinite}
.m-dots i:nth-child(2){animation-delay:var(--d-stagger)}
.m-dots i:nth-child(3){animation-delay:calc(var(--d-stagger) * 2)}
.m-load{font-size:12px;padding:var(--s-1) var(--s-2);border-radius:var(--r-sm);border:1px solid var(--info-line);background:var(--info-tint);color:var(--info-strong);animation:m-glow var(--d-pulse) var(--e-breathe) infinite}
.m-stag{display:flex;flex-direction:column;gap:var(--s-1);font-size:13px}
.m-stag span:first-child{color:var(--content-subtle);text-decoration:line-through}
.m-stag.run span{animation:m-swapin var(--d-swap) var(--e-out) both}
.m-stag.run span:last-child{animation-delay:var(--d-stagger);font-weight:600}
.curve{display:flex;flex-direction:column;gap:var(--s-2);padding:var(--s-3);border-radius:var(--r-sm);background:var(--surface)}
.curve svg{width:100%;height:96px;overflow:visible}
.curve .axis{fill:none;stroke:var(--line);stroke-width:1}
.curve .bez{fill:none;stroke:var(--action-primary);stroke-width:2;vector-effect:non-scaling-stroke}
.track{position:relative;display:block;height:10px;border-radius:var(--r-full);background:var(--canvas)}
.ball{position:absolute;top:0;left:0;width:10px;height:10px;border-radius:50%;background:var(--action-primary);animation:m-ball var(--d-pulse) infinite alternate}
.md{padding:0;overflow:hidden}
.md header{display:flex;align-items:center;gap:var(--s-2);padding:var(--s-3) var(--s-6);border-bottom:1px solid var(--canvas)}
.md header h2{margin:0 auto 0 0;font:500 14px/1 var(--mono)}
.act{all:unset;box-sizing:border-box;display:inline-flex;align-items:center;gap:var(--s-1);height:28px;padding:0 var(--s-3);border-radius:var(--r-full);border:1px solid var(--line);font-size:13px;font-weight:500;cursor:pointer;background:var(--surface-card)}
.act:hover{border-color:var(--line-strong)}
.act:focus-visible{outline:2px solid var(--action-primary);outline-offset:2px}
.md pre{margin:0;padding:var(--s-6) var(--s-6);max-height:640px;overflow:auto;font:400 12px/1.65 var(--mono);color:var(--content-muted);white-space:pre-wrap;overflow-wrap:anywhere}
.y-c{color:var(--content-subtle);opacity:.8}
.y-k{color:var(--info-strong)}
.y-s{color:var(--success-deep)}
.y-h{color:var(--content-primary);font-weight:600}
.y-i{color:var(--danger-deep)}
@media (max-width:720px){
  .wrap{padding:var(--s-6) var(--s-4) 64px}
  .fs{padding:var(--s-4)}
  .head{flex-wrap:wrap}
  .meta{margin-left:0;text-align:left;white-space:normal}
  .g2,.g3{grid-template-columns:minmax(0,1fr)}
  .srow{grid-template-columns:minmax(0,1fr);gap:var(--s-2) var(--s-3)}
  .sval{max-width:240px}
  .srow .sample{grid-column:1/-1}
  .snames{flex-direction:row;flex-wrap:wrap;justify-content:flex-start;align-items:center;gap:var(--s-2)}
  .g4{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
  .preview{padding:var(--s-4)}
  .md header{padding:var(--s-3) var(--s-4)}
  .md pre{padding:var(--s-4)}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:1ms!important;animation-iteration-count:1!important;transition-duration:1ms!important;scroll-behavior:auto!important}
  .btn:active{transform:none}
}
`;

const SCRIPT = `
document.addEventListener('click', async (event) => {
  const copy = event.target.closest('[data-copy]');
  if (copy) {
    try { await navigator.clipboard.writeText(copy.dataset.copy); } catch { return; }
    const tip = copy.querySelector('.tip');
    copy.setAttribute('data-copied', '');
    if (tip) tip.textContent = 'Скопировано';
    setTimeout(() => { copy.removeAttribute('data-copied'); if (tip) tip.textContent = 'Скопировать'; }, 1200);
    return;
  }
  const replay = event.target.closest('[data-replay]');
  if (replay) {
    const el = replay.parentElement.querySelector('[data-anim]');
    el.classList.remove('run');
    void el.offsetWidth;
    el.classList.add('run');
    return;
  }
  const toggle = event.target.closest('[data-toggle]');
  if (toggle) {
    toggle.setAttribute('aria-expanded', String(toggle.getAttribute('aria-expanded') !== 'true'));
    return;
  }
  const md = document.getElementById('design-md').textContent;
  const copyMd = event.target.closest('#copy-md');
  if (copyMd) {
    try { await navigator.clipboard.writeText(md); } catch { return; }
    copyMd.lastChild.textContent = ' Скопировано';
    setTimeout(() => { copyMd.lastChild.textContent = ' Скопировать'; }, 1200);
  }
  if (event.target.closest('#save-md')) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([md], { type: 'text/markdown' }));
    a.download = 'DESIGN.md';
    a.click();
    URL.revokeObjectURL(a.href);
  }
});
`;

function preview(spec) {
  const has = (name) => spec.components.some((c) => c.name === name);
  const badge = has('badge-success') ? 'badge-success' : 'badge-neutral';
  return `<div class="preview" aria-label="Пример экрана">
<div class="card">
<div class="eyebrow">Кампания</div>
<div class="row"><span class="title">Нейрокомментинг · Крипто</span><span class="bdg c-${badge}">слушает</span></div>
<input class="fld c-input" style="max-width:none" placeholder="Ссылка на канал" aria-label="Ссылка на канал">
<div class="cap">Комментарии уходят из 12 аккаунтов</div>
<div class="row"><button type="button" class="btn c-button-primary">Запустить</button><button type="button" class="btn c-button-secondary">Отмена</button><button type="button" class="btn c-button-ghost">Подробнее</button></div>
</div>
</div>`;
}

export function renderPage(spec, md, tokens) {
  const resolve = resolver(tokens);
  const componentStyles = spec.components
    .map((c) => componentCss(c.name, c.props, resolve))
    .join('\n');
  const total = spec.colors.reduce((n, g) => n + g.items.length, 0);
  return `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telebuba Design.md</title>
<!-- Собрано scripts/design-md.mjs из src/shared/design-system. Не править руками: npm run ds:doc -->
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root{
${rootVars(tokens)}
}
${CSS.trim()}
${TOP_CSS.trim()}
${componentStyles}
</style>
</head>
<body>
${topBar(
  'design',
  '<a href="#how">Где менять</a><a href="#colors">Цвета</a><a href="#typography">Типографика</a><a href="#spacing">Сетка</a><a href="#motion">Движение</a><a href="#components">Компоненты</a><a href="#design-md-h">DESIGN.md</a><a href="design-system.html">Канон</a>',
)}
<main class="wrap">
<div class="head"><span class="icon"><i></i></span><div><h1>Telebuba</h1><p>Операторский дашборд для Telegram: тёплый серый фон, белые карточки, один синий для действия и тона смысла с подложкой и рамкой.</p></div><div class="meta">${total} цветов · ${spec.typography.length} ролей текста · ${spec.components.length} компонентов<br>Источник: src/shared/design-system</div></div>
${preview(spec)}
${howToSection(spec)}
${colorsSection(spec)}
${typographySection(spec)}
${spacingSection(spec)}
${shapesSection(spec)}
${motionSection(spec)}
${componentsSection(spec, resolve)}
<section class="fs md" aria-labelledby="design-md-h">
<header><h2 id="design-md-h">DESIGN.md</h2><button type="button" class="act" id="save-md">Скачать</button><button type="button" class="act" id="copy-md"><span aria-hidden="true">⧉</span> Скопировать</button></header>
<pre id="design-md">${highlight(md)}</pre>
</section>
</main>
<script>${SCRIPT.trim()}</script>
</body>
</html>
`;
}
