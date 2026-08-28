// Состав шкал — из настоящего объекта токенов, а не из разбора файла.
//
// Этот модуль остался как одно место, где живёт СООТВЕТСТВИЕ «шкала Tailwind → экспорт
// токенов». Соответствие нетривиально (`borderRadius` — это `radius`, `spacing` — это
// `rhythm`, `zIndex` — это `layer`), и держать его двумя копиями — в `dead-tokens.mjs` и
// в правиле ESLint — значило бы повторить ту же ошибку на уровень выше: не значения, так
// названия шкал.
//
// Разбор по отступу, который здесь был, удалён целиком. Он не мог прочитать
// `flatColors`, где значение — ссылка `background.canvas`, а не литерал, и потому был бы
// первым, что сломалось от появления семантического уровня.
import { loadTokens } from './loadTokens.mjs';

const tokens = loadTokens();

// Шкала Tailwind → её объект в токенах. Ключи совпадают с `theme` в tailwind.config.ts, и
// это единственное место, где надо помнить, что во что переименовано.
const SCALES = {
  colors: tokens.flatColors,
  fontSize: tokens.fontSize,
  typeRole: tokens.typeRole,
  lineHeight: tokens.lineHeight,
  letterSpacing: tokens.letterSpacing,
  borderRadius: tokens.radius,
  boxShadow: tokens.shadow,
  transitionDuration: tokens.duration,
  transitionTimingFunction: tokens.easing,
  zIndex: tokens.layer,
  spacing: tokens.rhythm,
  size: tokens.size,
  height: tokens.height,
  width: tokens.width,
  minWidth: tokens.minWidth,
  maxWidth: tokens.maxWidth,
  minHeight: tokens.minHeight,
  maxHeight: tokens.maxHeight,
};

export function scale(name) {
  const found = SCALES[name];
  if (found === undefined) throw new Error(`tokens: шкала «${name}» не объявлена`);
  return found;
}

export function scaleNames(name) {
  return Object.keys(scale(name));
}

// Краска вложена на рунг глубже: `primary.DEFAULT` носится как `bg-primary`,
// `primary.tint` — как `bg-primary-tint`. Плоская краска — своим именем.
export function colorIds() {
  const ids = [];
  for (const [name, value] of Object.entries(tokens.flatColors)) {
    if (typeof value === 'string') {
      ids.push(name);
      continue;
    }
    for (const rung of Object.keys(value)) {
      ids.push(rung === 'DEFAULT' ? name : `${name}-${rung}`);
    }
  }
  return ids;
}

// Корни палитры, как их пишет класс. Именно корни: потребитель добирает рунг
// необязательным хвостом.
export function colorRoots() {
  return Object.keys(tokens.flatColors);
}

// Ступень размера и краска, которые тратит роль. Без этого рунг, который носят только
// роли, выглядел бы мёртвым.
export function roleRefs() {
  const refs = [];
  for (const role of Object.values(tokens.typeRole)) {
    refs.push(`fontSize.${role.size}`, `colors.${role.ink}`);
  }
  return refs;
}

export { tokens };
