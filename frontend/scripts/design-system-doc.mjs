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
    size: block(src, 'size'),
    height: block(src, 'height'),
    width: block(src, 'width'),
    minWidth: block(src, 'minWidth'),
    maxWidth: block(src, 'maxWidth'),
    minHeight: block(src, 'minHeight'),
    maxHeight: block(src, 'maxHeight'),
    fontSize: block(src, 'fontSize'),
    typeRole: block(src, 'typeRole'),
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

// Роли типографики: русская формулировка того, чем текст является читателю. Одно
// предложение на роль и без слова «или» — роль, которую нельзя описать одной фразой,
// это две роли, слитые размером. `sample` — то, что роль реально набирает в интерфейсе.
const TYPE_ROLE = {
  'page-title': {
    text: 'Заголовок, который называет экран. Шесть <code>&lt;h1&gt;</code>, по одному на страницу',
    sample: 'Прогрев',
  },
  'dialog-title': {
    text: 'Заголовок, с которого начинается диалог. Самая широко надетая роль набора: четыре слоя',
    sample: 'Удалить аккаунт?',
  },
  'dialog-body': {
    text: 'Фраза, которую диалог говорит перед кнопками. Своя роль, а не <code>prose</code>: все подтверждения приложения — и ConfirmModal, с которого их списали, — говорят её на ступень крупнее и на тон темнее, чем страница объясняет себя',
    sample: 'Аккаунт и его сессия будут удалены безвозвратно.',
  },
  'card-title': {
    text: 'Заголовок блока, в котором стоит: шапка CollapsibleCard, имя аккаунта на его карточке, название настройки над её описанием',
    sample: 'Каналы кампании',
  },
  'item-title': {
    text: 'Имя одного предмета внутри карточки: то, о чём строка, и то, о чём группа полей',
    sample: 'Основной прокси',
  },
  eyebrow: {
    text: 'Подпись, которая открывает группу настроек. Единственная роль с трекингом — четыре носителя сошлись на 0.04em, пятый уехал на 0.03em',
    sample: 'Сессия',
  },
  label: {
    text: 'Имя контрола, стоящего рядом: подпись поля, название настройки в строке',
    sample: 'Часовой пояс',
  },
  value: {
    text: 'Величина, которую строка показывает: ячейка таблицы, правая половина пары «ключ — значение»',
    sample: '+7 900 123-45-67',
  },
  prose: {
    text: 'Предложение, которое читает оператор: пояснение, пустое состояние, вопрос диалога',
    sample: 'Пока ничего не найдено',
  },
  caption: {
    text: 'Мелкая строка, которая уточняет контрол над собой: подсказка, единица, ошибка поля — когда берёт <code>text-danger</code>. Самая частая роль приложения',
    sample: 'Не больше 30 символов',
  },
  meta: {
    text: 'Самая мелкая строка: то, что датирует или считает строку рядом',
    sample: '18:42 · 12 сообщений',
  },
  stat: {
    text: 'Число, которое счётчик выносит на экран',
    sample: '1 284',
  },
};

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
    page: 'Воздух самой страницы: поля экрана входа, панели ошибки и блока «ничего нет»',
    empty: 'Пустое состояние: высота карточки, в которой нечего показать',
  },
  // Подписи размеров держатся одного правила: ступень называет роль, а роль обязана
  // указывать на компонент, который её носит. Ступень, про которую нельзя сказать, кто
  // её надевает, защитить нечем — и это единственная проверка, отличающая роль от
  // синонима величины.
  size: {
    tick: 'Самая мелкая метка: деления ползунка в WarmDaysModal, точка «печатает» в DialogueFeed',
    dot: 'Точка состояния рядом с подписью: Badge, прокси в таблице аккаунтов, индикатор системы в шапке',
    node: 'Метка шага на рельсе степпера: WarmingBoard и PipelineCard',
    spinner: 'Кольцо загрузки tb-spin и галочка пройденного шага степпера',
    glyph:
      'Круглый бейдж со знаком: «?» у HelpHint, флажок _CheckRow, номер шага в HowItWorksCard, бегунок ползунка',
    chip: 'Самое мелкое, что можно нажать: IconButton sm, кнопка удаления поверх фотографии в PhotoTab',
    icon: 'Отдельная иконочная кнопка: IconButton md, знак логотипа в шапке',
    tile: 'Иконка рядом с заголовком диалога, лицо аккаунта в строке таблицы, IconButton lg',
    thumbnail: 'Картинка поста в ChannelPostsPanel, плитка файла в AddAccountModal',
    touch: 'Цель для пальца: кнопки шапки на телефоне, строки NavDrawer, IconButton touch',
    face: 'Собственный портрет аккаунта в ProfileModal и AccountEdit',
  },
  height: {
    px: 'Волосяная линия: разделитель, который рисуют высотой, а не границей',
    full: 'Вся высота родителя — отказ от ступени, а не ступень',
    rail: 'Дорожка, которая заполняется: подчёркивание раздела в шапке, связка обоих степперов, шаги мастера в AddAccountModal',
    meter:
      'Полоса прогресса: прогрев в AccountEdit, ёмкость прокси в ProxyPool, рельс ползунка в WarmDaysModal',
    flag: 'Флаг страны — приложение рисовало его в шести размерах ради одной задачи, и только он: шкала расхода в NeuroAccountsModal мерила столько же, но шкала — не флаг',
    badge:
      'Счётчик, который растёт вширь, а не вверх: бейдж ListenerCard, номер фотографии в AddStoryModal',
    bar: 'Столбик гистограммы дней в WarmingBoard и компактные кнопки той же высоты',
    compact:
      'Контрол ниже поля: дорожка Switch, ползунок WarmDaysModal, кнопка удаления в ScenarioCard',
    header: 'Шапка приложения: AppNav и NavDrawer',
  },
  width: {
    px: 'Вертикальный разделитель в строке — на странице прогрева он единственный',
    auto: 'Ширина по содержимому: строка, которая на широком экране перестаёт быть колонкой',
    max: 'Ширина по самой длинной строке: подсказка над кнопкой в AddStoryModal',
    full: 'Вся ширина родителя — отказ от ступени, а не ступень',
    flag: 'Ширина флага страны',
    action:
      'Постоянная колонка, которую строка отдаёт контролу: кнопки запуска и удаления в CampaignsCard и ListenerCard, показ ключа в ApiKeyField, колонка флажков в DiscoveryResults. Дорожка Switch, полоса доверия в таблице аккаунтов и плитка раскладки в AddStoryModal мерили те же 46px, но ни одна не стоит в колонке строки, и каждая теперь своя',
    number: 'Числовое поле: дни и часы в WarmConfigModal, время в CommentModeFields',
    readout:
      'Число, которое не должно прыгать при изменении: показание ползунка в CampaignSetupCard, лимит в AccountLimitsModal. Ту же меру занимает карточка истории в AddStoryModal',
    stamp: 'Колонка таблицы со временем или идентификатором: LogsPage, канал в LogTerminal',
    col: 'Обычная колонка таблицы и поле, встроенное в строку',
    menu: 'Выпадающий список или фильтр рядом с заголовком страницы: меню аккаунта в AppNav',
    tip: 'Подсказка HelpHint и поле поиска на странице аккаунтов',
    confirm: 'Диалог-вопрос с двумя кнопками: ConfirmModal и три диалога удаления',
    form: 'Диалог, который заполняют: AddAccountModal, AddStoryModal, ProxyAddModal, WarmDaysModal',
    panel:
      'Диалог со списком или вкладками: ProfileModal, ChannelEditModal, NeuroAccountsModal, WarmConfigModal',
    table:
      'Диалог вокруг таблицы: история комментариев и подбор каналов. 926 — это 880 порога DataTable плюс собственные поля более щедрого из двух: ниже диалог показал бы карточки, а не таблицу',
  },
  minWidth: {
    0: 'Ноль, который позволяет ячейке flex обрезать текст вместо того, чтобы растягивать строку. Самый частый размерный токен в приложении',
    badge: 'Счётчик, который обязан остаться круглым на одной цифре: ListenerCard, AddStoryModal',
    col: 'Ширина, ниже которой колонка не опускается: шапка AccountEdit, фильтр на странице аккаунтов',
    table:
      'Порог, ниже которого DataTable перестаёт быть таблицей и становится карточками, — и потому мера, вокруг которой считается ширина диалога с таблицей',
  },
  maxWidth: {
    full: 'Не шире родителя: диалог на узком экране',
    name: 'Предел для канала или комментария, который обязан обрезаться: DiscoveryResults, NeurocommentBoard',
    page: 'Содержательная колонка страницы: AccountEdit, NeuroshillingPage',
    shell: 'Собственная ширина приложения: AppShell и AppNav',
  },
  minHeight: {
    touch: 'Те же 44px, что и у квадратной ступени, но про одну ось: строки NavDrawer',
    screen: 'Ростом со стекло — корневой контейнер приложения',
  },
  maxHeight: {
    feed: 'Прокручиваемый список, у которого свой блок: LogTerminal, комментарии NeurocommentBoard, аккаунты CampaignPromptModal, DialogueFeed',
    dialog:
      'Предел для тела диалога. В dvh, потому что панели браузера на телефоне — часть того, что ему надо обойти',
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
  'двенадцать',
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

// Роли рисуются из конфига целиком: размер берётся из ступени, которую роль называет,
// цвет — из токена, а не из своего значения, поэтому подкрутка `ink-subtle` доезжает и
// до образца на этой странице. Роль без русской подписи выводится с пустой ячейкой —
// это и есть сигнал, что в конфиг добавили имя, которому ещё не назначили смысл.
function renderTypeRoles(config, indent) {
  const sizes = Object.fromEntries(config.fontSize.map((e) => [e.name, e.value]));
  const prose = `${count(config.typeRole.length, 'ролей')}, и над <code>shared/ui</code> страница называет одну из них вместо того, чтобы заново выписывать ступень, начертание и серый. Заменяемая запись была не восемью ступенями, а девяноста шестью написаниями: 528 мест писали ступень рядом с начертанием и чернилами, и одна и та же задача выходила тремя способами сразу — подпись была <code>ink-subtle</code> 53 раза, <code>ink-muted</code> 13 и без цвета 9. Роль обязана называться одним предложением без слова «или» и быть надетой двумя компонентами в разных слоях; именно это удержало набор на двенадцати. Межстрочное в роль не входит — по той же причине, по которой его нет в ступени.`;
  const rows = config.typeRole.map((entry) => {
    const role = Object.fromEntries(entry.children.map((c) => [c.name, c.value]));
    const note = TYPE_ROLE[entry.name] ?? { text: '', sample: '' };
    const spec = [
      `${px(sizes[role.size])} · ${role.weight} · ${role.ink}`,
      role.tracking === undefined ? '' : ` · ${role.tracking}`,
      role.caps === undefined ? '' : ' · заглавные',
    ].join('');
    const style = [
      `font-size:${sizes[role.size]}`,
      `font-weight:${role.weight}`,
      `color:var(--${role.ink})`,
      role.tracking === undefined ? '' : `;letter-spacing:${role.tracking}`,
      role.caps === undefined ? '' : `;text-transform:${role.caps}`,
    ]
      .join(';')
      .replace(/;;/g, ';')
      .replace(/;$/, '');
    return specRow(
      `${indent}  `,
      `<code>type-${entry.name}</code><br><span class="n" style="color:var(--ink-subtle);font-size:11.5px">${spec}</span>`,
      `<span style="${style}">${note.sample}</span><br><span class="n" style="color:var(--ink-subtle);font-size:11.5px">${note.text}</span>`,
    );
  });
  return [
    `${indent}<p class="body">${prose}</p>`,
    `${indent}<table class="spec">`,
    ...rows,
    `${indent}</table>`,
  ].join('\n');
}

function renderSpacingScale(config, indent) {
  // `0` и `px` из таблицы выпадают: проза называет их не ступенями, и посчитать их
  // ступенями значило бы написать над таблицей число, которое сама же проза опровергает.
  const rungs = config.spacing.filter((e) => e.name !== '0' && e.name !== 'px');
  const prose = `${count(rungs.length, 'ступеней')} — один ритм на зазор, отступ и поле. Зазор и отступ это одна мера с двух сторон, и держать их в разных шкалах значит получить <code>gap-md</code> рядом с <code>px-3</code> в одной строке. Числа взяты из макета, а не с сетки в 4px: у приложения было два ритма, свой и Tailwind, и выигрывает тот, который рисовали. Шкала <em>заменяет</em> числовую шкалу Tailwind целиком, и до сих пор это было невозможно: <code>spacing</code> кормит и <code>w-*</code>/<code>h-*</code> тоже, а 34px аватара — размер компонента, а не ступень ритма. Теперь у размеров свои шкалы, поэтому <code>p-md</code> рисует 10px, а <code>w-md</code> не рисует ничего. <code>0</code> и <code>px</code> в конфиге есть, но в таблице их нет: это не ступени.`;
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

function renderSizeScale(config, indent) {
  const prose = `${count(config.size.length, 'ступеней')} на квадрат, где ширина и высота — одно решение. 131 элемент писал одно и то же число дважды парой <code>w</code>/<code>h</code>; <code>size-*</code> говорит его один раз. Эти ступени вобрали двадцать девять диаметров кружков и все иконочные коробки приложения, и ни одна свёртка не сдвинула значение больше чем на 4px.`;
  const rows = config.size.map((e) =>
    specRow(indent + '  ', `<code>${e.name}</code> · ${px(e.value)}`, RUNG.size[e.name] ?? ''),
  );
  return [
    `${indent}<p class="body">${prose}</p>`,
    `${indent}<table class="spec">`,
    ...rows,
    `${indent}</table>`,
  ].join('\n');
}

// Остальные размерные шкалы: заголовок, вступление и таблица на каждую. Значение
// печатается как есть — в одной шкале стоят рядом пиксели, проценты, dvh и ключевые
// слова, и обрезать «px» тут значило бы врать в трёх строках из четырёх.
const DIMENSION_GROUPS = [
  [
    'height',
    'Высоты',
    'Высота, которая не является стороной квадрата: предмет либо лежит (дорожка, полоса), либо стоит в собственный рост контрола.',
  ],
  [
    'width',
    'Ширины',
    'Ширины — это в основном полосы, а не размеры: колонка, обрезка, диалог. Последние четыре ступени и есть причина, по которой шкала существует: двадцать два диалога тратили одиннадцать ширин — 380, 420, 440, 460, 468, 480, 540, 560, 580, 760 и 920, — а это не шкала, а протокол того, кто с какой родился.',
  ],
  [
    'minWidth',
    'Нижние границы ширины',
    'Ширина, ниже которой элемент не опускается, что бы ни делал flex вокруг него.',
  ],
  [
    'maxWidth',
    'Верхние границы ширины',
    'Пределы, и все они про чтение, а не про то, чтобы влезть.',
  ],
  ['minHeight', 'Нижние границы высоты', ''],
  ['maxHeight', 'Пределы прокрутки', ''],
];

function renderDimensionScales(config, indent) {
  const out = [
    `${indent}<p class="body">Размер компонента — не ступень ритма, и до сих пор это было мнение, а не устройство: в Tailwind ключ <code>spacing</code> становится и <code>p-&lt;имя&gt;</code>, и <code>w-&lt;имя&gt;</code>, его разливают семнадцать базовых шкал. Пока одна таблица отвечала на два вопроса, 476 размерных мест тратили 73 разных значения, 57 из которых ни одна ступень ритма не называла. Шкалы ниже объявлены отдельно и <code>spacing</code> не разливают, поэтому <code>p-md</code> есть, а <code>w-md</code> нет.</p>`,
    `${indent}<p class="body">Каждое имя здесь — <em>роль</em>, и проверка у роли одна: она обязана называть то, что в этом продукте есть, и надо уметь показать компонент, который её носит. <code>coin</code> для 52px проверку не проходит, <code>touch</code> для цели пальца в 44px — проходит. Слово-величина не умеет спорить со следующим значением, а спорить со значениями — вся работа шкалы.</p>`,
  ];
  for (const [key, title, prose] of DIMENSION_GROUPS) {
    out.push(`${indent}<h3>${title}</h3>`);
    if (prose) out.push(`${indent}<p class="body">${prose}</p>`);
    out.push(`${indent}<table class="spec">`);
    for (const entry of config[key]) {
      out.push(
        specRow(
          `${indent}  `,
          `<code>${entry.name}</code> · ${entry.value}`,
          RUNG[key][entry.name] ?? '',
        ),
      );
    }
    out.push(`${indent}</table>`);
  }
  return out.join('\n');
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
  'type-roles': (config) => renderTypeRoles(config, '      '),
  'radius-scale': (config) => renderRadiusScale(config, '        '),
  'spacing-scale': (config) => renderSpacingScale(config, '      '),
  'size-scale': (config) => renderSizeScale(config, '      '),
  'dimension-scale': (config) => renderDimensionScales(config, '      '),
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
