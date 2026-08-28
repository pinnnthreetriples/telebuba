// Единственный объект токенов — тот, который читают ВСЕ проверки и потребители.
//
// «Один источник правды» ломался не на первом экземпляре, а на шестом: значения жили в
// `tailwind.config.ts`, а состав шкал повторяли `cn.ts` (списком), правило ESLint
// (списком), `dead-tokens.mjs` (разбором) и генератор документации (разбором). Каждая
// копия расходилась молча и по-своему: список в `cn.ts` перестаёт разрешать конфликт
// классов, список в правиле перестаёт прикрывать новый цвет, разбор падает от смены
// отступа. Теперь состав один, и берут его отсюда.
export * as motion from './motion';
export * as primitives from './primitives';
export * as semantic from './semantic';
export * as spacing from './spacing';
export * as typography from './typography';

export { layer, radius, shadow, font, palette } from './primitives';
export { flatColors, background, content, border, action, feedback, inverse } from './semantic';
export { fontSize, typeRole, lineHeight, letterSpacing } from './typography';
export { rhythm, size, height, width, minWidth, maxWidth, minHeight, maxHeight } from './spacing';
export { duration, easing } from './motion';

export type { TypeRoleName } from './names';
export {
  TYPE_ROLE_NAMES,
  RHYTHM_NAMES,
  FONT_SIZE_NAMES,
  RADIUS_NAMES,
  LINE_HEIGHT_NAMES,
  TRACKING_NAMES,
  COLOUR_ROOTS,
} from './names';
