// Держит docs/design-system.html в согласии с tailwind.config.ts.
//
// Документ писался руками и от этого отстал: пять слитых веток поменяли значения
// токенов и добавили новые, а страница осталась со старыми. Половина её содержимого
// — это значения из конфига, и её незачем набирать второй раз. Скрипт берёт эту
// половину из конфига и подставляет между парами меток `ds:gen <имя>`; всё, что вне
// меток, — живые примеры компонентов, принципы, свод унификации и план работ —
// скрипт не трогает.
//
// Конфиг читается как текст, а не импортируется: импорт .ts из Node требует либо
// загрузчика в зависимостях, либо флага снятия типов и достаточно свежего Node.
// Гейт, который падает от версии рантайма, хуже отсутствующего гейта, а нужных
// форм записи в конфиге всего две — строка и вложенный объект.
//
// Вывод побайтово устойчив: порядок — как в конфиге, переводы строк LF, ничего
// зависящего от времени запуска. Поэтому разница в diff означает разницу в системе,
// а `--check` можно ставить в гейты.

import { readFileSync, writeFileSync } from 'node:fs';

const CONFIG_PATH = new URL('../tailwind.config.ts', import.meta.url);
const DOC_PATH = new URL('../docs/design-system.html', import.meta.url);

/* ── Разбор конфига ───────────────────────────────────────────────────────── */

// Значения в конфиге — либо строка в одинарных кавычках, либо вложенный объект.
// Больше форм в разбираемых блоках не встречается, и появление новой должно
// оборвать генерацию, а не молча выпасть из документа.
function parseObject(src, open) {
  const entries = [];
  let i = open + 1;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '}') return { entries, end: i + 1 };
    if (ch === ',' || /\s/.test(ch)) {
      i += 1;
      continue;
    }
    if (ch === '/' && src[i + 1] === '/') {
      i = src.indexOf('\n', i) + 1;
      continue;
    }
    const key = /^(['"]?)([\w$-]+)\1\s*:\s*/.exec(src.slice(i));
    if (!key) throw new Error(`tailwind.config.ts: не разобрать ключ у «${src.slice(i, i + 40)}»`);
    i += key[0].length;
    if (src[i] === '{') {
      const inner = parseObject(src, i);
      entries.push({ name: key[2], children: inner.entries });
      i = inner.end;
    } else {
      const value = /^'([^']*)'/.exec(src.slice(i));
      if (!value) throw new Error(`tailwind.config.ts: у «${key[2]}» не строковое значение`);
      entries.push({ name: key[2], value: value[1] });
      i += value[0].length;
    }
  }
  throw new Error('tailwind.config.ts: объект не закрыт');
}

function block(src, key) {
  const at = src.search(new RegExp(`^[ \\t]*${key}: \\{$`, 'm'));
  if (at < 0) throw new Error(`tailwind.config.ts: блок «${key}» не найден`);
  return parseObject(src, src.indexOf('{', at)).entries;
}

function readConfig() {
  const src = readFileSync(CONFIG_PATH, 'utf8');
  return {
    colors: block(src, 'colors'),
    spacing: block(src, 'spacing'),
    fontSize: block(src, 'fontSize'),
    borderRadius: block(src, 'borderRadius'),
    boxShadow: block(src, 'boxShadow'),
    transitionDuration: block(src, 'transitionDuration'),
    transitionTimingFunction: block(src, 'transitionTimingFunction'),
    zIndex: block(src, 'zIndex'),
  };
}

/* ── Русские подписи ──────────────────────────────────────────────────────── */

// Значения и порядок приходят из конфига; здесь — только роль токена по-русски.
// Конфиг комментирован по-английски, документ написан по-русски, и машинного
// перевода тут быть не должно, поэтому текст роли остаётся ручным. Токен без
// подписи выводится без неё — пустая строка в свотче и есть сигнал, что в конфиге
// появилось имя, которому ещё не назначили смысл.
const ROLE = {
  white: 'Карточки, поля, шапка',
  surface: 'Шапка таблицы, наведение на строку, вложенный блок',
  canvas: 'Фон страницы, рельс под плашкой и всё заполняемое: дорожка прогресса, чип, счётчик',
  scrim:
    'Завеса над фотографией: тёмные чернила на 55%, чтобы кнопка поверх снимка осталась читаемой',
  ink: 'Заголовки, основной текст',
  'ink-body': 'Плотные строки, значения в чипах',
  'ink-muted': 'Второстепенный текст, контурные кнопки',
  'ink-subtle': 'Подписи, плейсхолдеры, иконки в покое',
  line: 'Граница по умолчанию, в том числе у поля ввода',
  'line-strong': 'Наведение, пунктир, полоса прокрутки',
  'line-row': 'Разделитель строк таблицы',
  primary: 'Действие, живое состояние, фокус',
  'primary-press': 'Наведение и нажатие залитой кнопки',
  'primary-tint': 'Выбранная строка, синий чип, заливка наведения',
  'primary-line': 'Граница синей подложки и пунктира',
  'primary-hairline': 'Рамка настолько бледная, что работает и разделителем плитки',
  'primary-deep': 'Синий для мелкого текста на tint: основной даёт там 4.38:1',
  success: 'Работает, проверено',
  'success-deep': 'Заголовок на зелёной подложке — единственный проходит по читаемости',
  'success-press': 'Наведение и нажатие залитой зелёной кнопки',
  'success-tint': 'Плашка «слушает», зелёный чип',
  'success-line': 'Граница зелёной подложки',
  'success-dot': 'Точка «система активна»',
  warning: 'Пауза, ожидание, спам-блок',
  'warning-deep': 'Заголовок или иконка на янтарном чипе',
  'warning-strong': 'Точка и значок, где нужен более яркий янтарный',
  'warning-tint': 'Подложка предупреждения',
  'warning-line': 'Граница янтарной подложки',
  danger: 'Ошибка, удаление, бан',
  'danger-deep': 'Красный для мелкого текста на красном чипе: каждая метка «удалён» набрана 10.5px',
  'danger-tint': 'Лицо разрушительной кнопки',
  'danger-line': 'Граница красной подложки',
  term: 'Журнал, тёмная подсказка, тост',
  'term-dim': 'Время и разделители в строке журнала',
  'term-text': 'Текст журнала',
  'term-link': 'Канал и ссылка в журнале',
  'term-error': 'Сбой в журнале',
  'term-success': 'Удачный исход в журнале',
  'term-warning': 'Предупреждение в журнале',
};

// Группы свотчей: заголовок и то, какие ключи конфига в него попадают. Ключ, не
// названный ни в одной группе, уходит в последнюю — новый токен обязан появиться
// на странице сам, даже если ему ещё не выбрали раздел.
const SWATCH_GROUPS = [
  {
    title: 'Основа',
    keys: ['canvas', 'scrim', 'surface'],
    prose:
      'Тон отвечает на вопрос «где я нахожусь»: белое — предмет, <strong>surface</strong> — вложено в предмет, <strong>canvas</strong> — под предметом и внутри него всё, что заполняется. Отдельного тона для заполняемого нет: он отличался от <strong>canvas</strong> на три единицы, а на фоне страницы ни один чип в приложении не лежит. <strong>scrim</strong> стоит особняком: это не тон основы, а завеса поверх фотографии, единственное полупрозрачное значение в наборе.',
  },
  {
    title: 'Текст',
    keys: ['ink'],
    prose:
      '<strong>muted</strong> и <strong>subtle</strong> — два серых, которыми набран мелкий текст, и оба стоят на пороге AA, а не там, где выглядели лучше: <strong>muted</strong> давал 4.10:1 на заливке чипа, <strong>subtle</strong> — 2.88:1 на белом. Запас между ними и <strong>body</strong> сжат намеренно; другой выход — ступень, про которую система знает, что её не прочесть.',
  },
  { title: 'Линии', keys: ['line'], prose: '' },
  {
    title: 'Смысл',
    keys: ['primary', 'success', 'warning', 'danger', 'term'],
    prose:
      'У каждого смысла три роли: <strong>основной</strong> — текст и иконка, <strong>tint</strong> — подложка, <strong>line</strong> — граница подложки. Синий добавляет <strong>press</strong>, наведение на залитую кнопку, а его <strong>tint</strong> работает заодно и заливкой наведения. <strong>deep</strong> появляется там, где основной оттенок не проходит по контрасту на собственной подложке. <strong>term</strong> — единственная тёмная поверхность: журнал и подсказки, которые делят с ним чернила.',
  },
  { title: 'Прочее', keys: [], prose: '' },
];

const RUNG = {
  fontSize: {
    micro: 'Подпись под строкой, вторая строка плашки',
    tiny: 'Пилюля статуса, шапка таблицы, журнал',
    body: 'Основной размер интерфейса',
    lead: 'Поле, список, подпись кнопки, заголовок карточки',
    title: 'Заголовок раздела и диалога',
    stat: 'Счётчик-одометр',
    display: 'Заголовок страницы',
    hero: 'Единственная крупная цифра',
  },
  // Радиусы и тени подписываются внутри <i> под именем ступени, а там документ
  // пишет со строчной: это продолжение подписи, а не отдельная фраза.
  borderRadius: {
    none: 'отсутствие скругления, а не ступень',
    sm: 'чип-кнопка, иконка 24',
    md: 'иконка 28, подсказка',
    lg: 'поле, вложенная карточка, плашка',
    card: 'карточка, диалог',
    full: 'действие, статус, чип',
  },
  spacing: {
    hair: 'Волосяной зазор сетки и стопки строк',
    xs: 'Счётчик над своей подписью',
    tight: 'Иконка и подпись, точка и текст',
    sm: 'Основной зазор внутри контрола и строки',
    md: 'Строки и поля внутри карточки',
    lg: 'Блоки карточки и формы',
    xl: 'Горизонтальный отступ контрола',
    '2xl': 'Горизонтальный отступ крупного контрола',
    '3xl': 'Воздух страницы',
    '4xl': 'Воздух страницы, крупный шаг',
    '5xl': 'Пустое состояние',
  },
  zIndex: {
    0: 'Выход из слоёв: едущая капсула лежит под своими подписями',
    raised: 'Содержимое поверх соседей в том же потоке',
    sticky: 'Шапка приложения',
    pop: 'Выпадающий список, подсказка, меню',
    dialog: 'Диалог и подложка. Порядок вложенных диалогов задаёт порядок монтирования, а не z',
  },
  boxShadow: {
    none: 'отсутствие тени, а не высота',
    pop: 'всплывает над страницей: список, подсказка, тост, меню',
    ring: 'волосяной контур вместо границы на подложке',
    thumb: 'палец, который тянут',
    focus: 'кольцо на поле и списке',
    seg: 'поднятый сегмент вложенного лотка',
    pill: 'едущая капсула сегментной группы: лежит на лотке, а не в нём',
  },
  // %curve% — место, куда подставляется кривая: имя ступени берётся здесь, а её
  // значение из конфига, чтобы длительность и кривая одного жеста не разъехались.
  transitionDuration: {
    state: { text: 'Смена состояния: цвет, граница, фон', curve: '' },
    enter: { text: 'Появление: диалог, строка, событие, тост. Одно на всю систему', curve: '' },
    reveal: {
      text: 'Шеврон и раскрытие карточки — %curve%. Один жест — одна длительность на обе половины',
      curve: 'spring',
    },
    roll: {
      text: 'Одометр докручивается — %curve%. Плашка с действиями едет на <code>reveal</code>',
      curve: 'out',
    },
  },
};

/* ── Отрисовка ────────────────────────────────────────────────────────────── */

const COUNT = [
  'ноль',
  'одна',
  'две',
  'три',
  'четыре',
  'пять',
  'шесть',
  'семь',
  'восемь',
  'девять',
  'десять',
  'одиннадцать',
];

// Число ступеней тоже приходит из конфига: «четыре ступени» над таблицей из
// одиннадцати строк — это ровно тот способ устареть, от которого здесь защита.
function count(n, forms) {
  const word = COUNT[n] ?? String(n);
  return `${word[0].toUpperCase()}${word.slice(1)} ${forms}`;
}

// Свотч у документа один: цветной прямоугольник, имя, значение, роль. Роль может
// отсутствовать — тогда её место остаётся пустым, а не заполняется догадкой.
function swatch(indent, name, value) {
  const role = ROLE[name] ?? '';
  return `${indent}<div class="sw"><i style="background:${value}"></i><b>${name}</b><em>${value}</em><span>${role}</span></div>`;
}

function flattenColors(entries) {
  const flat = [];
  for (const entry of entries) {
    if (!entry.children) {
      flat.push({ group: entry.name, name: entry.name, value: entry.value });
      continue;
    }
    for (const child of entry.children) {
      const name = child.name === 'DEFAULT' ? entry.name : `${entry.name}-${child.name}`;
      flat.push({ group: entry.name, name, value: child.value });
    }
  }
  return flat;
}

function renderColorSwatches(config, indent) {
  const flat = flattenColors(config.colors);
  const named = new Set(SWATCH_GROUPS.flatMap((g) => g.keys));
  const out = [];
  for (const group of SWATCH_GROUPS) {
    const rows =
      group.keys.length > 0
        ? flat.filter((c) => group.keys.includes(c.group))
        : flat.filter((c) => !named.has(c.group));
    // Белое живёт в палитре Tailwind, а не в конфиге: перекрывать его нечем, и
    // в основе оно первое по смыслу, поэтому дописывается здесь, а не читается.
    if (group.title === 'Основа') rows.unshift({ name: 'white', value: '#ffffff' });
    if (rows.length === 0) continue;
    out.push(`${indent}<h3>${group.title}</h3>`);
    if (group.prose) out.push(`${indent}<p class="body">${group.prose}</p>`);
    out.push(`${indent}<div class="sw-grid">`);
    for (const row of rows) out.push(swatch(`${indent}  `, row.name, row.value));
    out.push(`${indent}</div>`);
    out.push('');
  }
  out.pop();
  return out.join('\n');
}

function specRow(indent, label, text) {
  return `${indent}<tr><td>${label}</td><td>${text}</td></tr>`;
}

function px(value) {
  return value.replace(/px$/, '');
}

function renderTypeScale(config, indent) {
  return config.fontSize
    .map((e) =>
      specRow(indent, `<code>${e.name}</code> · ${px(e.value)}`, RUNG.fontSize[e.name] ?? ''),
    )
    .join('\n');
}

function renderSpacingScale(config, indent) {
  const rungs = config.spacing;
  const prose = `${count(rungs.length, 'ступеней')} — один ритм на зазор, отступ и поле. Зазор и отступ это одна мера с двух сторон, и держать их в разных шкалах значит получить <code>gap-md</code> рядом с <code>px-3</code> в одной строке. Числа взяты из макета, а не с сетки в 4px: у приложения было два ритма, свой и Tailwind, и выигрывает тот, который рисовали. Шкала <em>добавлена</em>, а не заменена — <code>spacing</code> кормит ещё и <code>w-*</code>/<code>h-*</code>, а 34px аватара это размер компонента, а не ступень ритма; вернуться <code>p-4</code> не даёт правило линтера, а не отсутствие ключа.`;
  const rows = rungs.map((e) =>
    specRow(indent + '  ', `<code>${e.name}</code> · ${px(e.value)}`, RUNG.spacing[e.name] ?? ''),
  );
  return [
    `${indent}<p class="body">${prose}</p>`,
    `${indent}<table class="spec">`,
    ...rows,
    `${indent}</table>`,
  ].join('\n');
}

function renderLayerScale(config, indent) {
  const rungs = config.zIndex;
  const prose = `${count(rungs.length, 'ступеней')}, и порядок в них — это утверждение о поведении: всплывающая панель обязана быть выше липкой шапки, иначе выпадающий список уезжает под неё.`;
  const rows = rungs.map((e) =>
    specRow(indent + '  ', `<code>${e.name}</code> · ${e.value}`, RUNG.zIndex[e.name] ?? ''),
  );
  return [
    `${indent}<p class="body">${prose}</p>`,
    `${indent}<table class="spec">`,
    ...rows,
    `${indent}</table>`,
  ].join('\n');
}

function renderRadiusScale(config, indent) {
  return config.borderRadius
    .map(
      (e) =>
        `${indent}<div><div style="width:56px;height:38px;background:var(--primary-tint);border:1px solid var(--primary-line);border-radius:${e.value}"></div><span class="cap">${e.name} · ${px(e.value)}<i>${RUNG.borderRadius[e.name] ?? ''}</i></span></div>`,
    )
    .join('\n');
}

function renderShadowScale(config, indent) {
  const rungs = config.boxShadow;
  const prose = `${count(rungs.length, 'высот')}, по одной на задачу. У плоского интерфейса тень — способ сказать «я поверх», а не украшение, поэтому высота даётся смыслу, а не элементу: тост, подсказка, меню и список всплывают одинаково, потому что делают одно и то же.`;
  // Геометрия у всех образцов одна: из конфига выводится тень, а не форма
  // элемента, и разная подложка под разными тенями сравнивала бы не то.
  const wells = rungs.map(
    (e) =>
      `${indent}  <div><span style="width:88px;height:34px;border-radius:11px;background:var(--white);border:1px solid var(--line);box-shadow:${e.value};display:block"></span><span class="cap">${e.name}<i>${RUNG.boxShadow[e.name] ?? ''}</i></span></div>`,
  );
  return [
    `${indent}<p class="body">${prose}</p>`,
    `${indent}<div class="wells w2" style="margin-top:16px">`,
    ...wells,
    `${indent}</div>`,
  ].join('\n');
}

function renderMotionScale(config, indent) {
  const curves = Object.fromEntries(config.transitionTimingFunction.map((e) => [e.name, e.value]));
  return config.transitionDuration
    .map((e) => {
      const note = RUNG.transitionDuration[e.name] ?? { text: '', curve: '' };
      const text = note.curve
        ? note.text.replace(
            '%curve%',
            `<code>${note.curve}</code>, <code>${curves[note.curve]}</code>`,
          )
        : note.text;
      return specRow(indent, `<code>${e.name}</code> · ${e.value}`, text);
    })
    .join('\n');
}

// Документ рисует живые примеры компонентов на собственных переменных, поэтому
// значения токенов должны стоять и здесь, иначе свотчи показывают одно, а кнопки
// рядом — другое. Имена переменных оставлены те, которыми документ уже написан:
// тёмная поверхность зовётся у него slate, ссылка в журнале — slate-blue, радиус
// вложенного блока — nest. Переименовать их значит переписать авторскую половину.
const VAR_ALIAS = { term: 'slate', 'term-link': 'slate-blue', lg: 'nest' };

function rootLines(prefix, entries) {
  return entries.map((e) => `  --${prefix}${e.name}:${e.value};`);
}

function renderRootTokens(config) {
  const colors = flattenColors(config.colors).map((c) => ({
    name: VAR_ALIAS[c.name] ?? c.name.replace(/^term/, VAR_ALIAS.term),
    value: c.value,
  }));
  const radii = config.borderRadius.map((e) => ({
    name: VAR_ALIAS[e.name] ?? e.name,
    value: e.value,
  }));
  return [
    '  /* Цвет */',
    '  --white:#ffffff;',
    ...colors.map((c) => `  --${c.name}:${c.value};`),
    '',
    '  /* Шрифт */',
    "  --sans:'Inter',-apple-system,system-ui,sans-serif;",
    "  --mono:'JetBrains Mono',ui-monospace,monospace;",
    '',
    '  /* Форма */',
    ...radii.map((r) => `  --r-${r.name}:${r.value};`),
    '',
    '  /* Движение */',
    ...rootLines('t-', config.transitionDuration),
    ...rootLines('e-', config.transitionTimingFunction),
    '',
    '  /* Высота */',
    // `none` переменной не получает: «нет тени» — это не значение, которое
    // подставляют, а отсутствие правила.
    ...config.boxShadow.filter((e) => e.name !== 'none').map((e) => `  --${e.name}:${e.value};`),
  ].join('\n');
}

/* ── Подстановка ──────────────────────────────────────────────────────────── */

// Метки одинаковы по тексту и различаются только обёрткой: внутри <style> она
// должна быть комментарием CSS, в разметке — комментарием HTML. Ищется текст,
// поэтому обе формы обслуживает один проход.
const REGIONS = {
  'root-tokens': (config) => renderRootTokens(config),
  'color-swatches': (config) => renderColorSwatches(config, '      '),
  'type-scale': (config) => renderTypeScale(config, '          '),
  'radius-scale': (config) => renderRadiusScale(config, '        '),
  'spacing-scale': (config) => renderSpacingScale(config, '      '),
  'layer-scale': (config) => renderLayerScale(config, '      '),
  'shadow-scale': (config) => renderShadowScale(config, '      '),
  'motion-scale': (config) => renderMotionScale(config, '        '),
};

function replaceRegion(doc, name, body) {
  const open = doc.indexOf(`ds:gen ${name} `);
  const close = doc.indexOf(`/ds:gen ${name} `);
  if (open < 0 || close < 0 || close < open) {
    throw new Error(`design-system.html: нет пары меток «ds:gen ${name}»`);
  }
  const from = doc.indexOf('\n', open) + 1;
  const to = doc.lastIndexOf('\n', close) + 1;
  return doc.slice(0, from) + body + '\n' + doc.slice(to);
}

function generate() {
  const config = readConfig();
  let doc = readFileSync(DOC_PATH, 'utf8').replace(/\r\n/g, '\n');
  for (const [name, render] of Object.entries(REGIONS)) {
    doc = replaceRegion(doc, name, render(config));
  }
  return doc;
}

function main() {
  const check = process.argv.includes('--check');
  const wanted = generate();
  const actual = readFileSync(DOC_PATH, 'utf8');
  if (!check) {
    if (actual !== wanted) writeFileSync(DOC_PATH, wanted, 'utf8');
    process.stdout.write(
      actual === wanted
        ? 'design-system.html: уже соответствует tailwind.config.ts\n'
        : 'design-system.html: обновлён из tailwind.config.ts\n',
    );
    return 0;
  }
  if (actual === wanted) {
    process.stdout.write('design-system.html: соответствует tailwind.config.ts\n');
    return 0;
  }
  // Расхождение показывается построчно: увидеть надо не «файлы разные», а какое
  // именно значение в документе больше не то, что рисует приложение.
  const from = actual.split('\n');
  const to = wanted.split('\n');
  const lines = [];
  for (let i = 0; i < Math.max(from.length, to.length); i += 1) {
    if (from[i] === to[i]) continue;
    if (from[i] !== undefined) lines.push(`  документ ${i + 1}: ${from[i].trim()}`);
    if (to[i] !== undefined) lines.push(`  конфиг   ${i + 1}: ${to[i].trim()}`);
  }
  process.stderr.write(
    'design-system.html разошёлся с tailwind.config.ts.\n' +
      `${lines.slice(0, 40).join('\n')}\n` +
      (lines.length > 40 ? `  … и ещё строк: ${lines.length - 40}\n` : '') +
      'Собрать заново: npm run ds:doc\n',
  );
  return 1;
}

process.exit(main());
