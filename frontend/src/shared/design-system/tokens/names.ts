// Состав шкал как СПИСОК ИМЁН — то, что нужно потребителям, которым не нужны значения.
//
// Их два: `cn.ts` объясняет tailwind-merge, какие значения принадлежат какой группе
// утилит, а правило `design-tokens/no-raw-values` собирает из имён регулярки. Оба до
// этого файла держали имена литеральными списками, и оба ломались одинаково — не
// перекрашивая ничего, а переставая работать: незнакомое tailwind-merge значение не
// вступает в конфликт ни с чем, и `cn('py-tight', 'py-xs')` оставлял ОБА класса, отдавая
// победу порядку в таблице стилей; незнакомое правилу имя цвета просто не прикрывается.
//
// Списки ВЫЧИСЛЯЮТСЯ из самих шкал, поэтому добавленная ступень попадает в них сама. Это
// и есть разница с прежним устройством: расхождение было возможно не при переименовании
// (его заметили бы), а при ДОБАВЛЕНИИ — новый рунг появлялся в конфиге, список не рос, и
// проверка тихо начинала смотреть на меньшее.
import { radius } from './primitives';
import { flatColors } from './semantic';
import { rhythm } from './spacing';
import { fontSize, letterSpacing, lineHeight, typeRole } from './typography';

export type TypeRoleName = keyof typeof typeRole;

export const TYPE_ROLE_NAMES = Object.keys(typeRole) as TypeRoleName[];
export const RHYTHM_NAMES = Object.keys(rhythm);
export const FONT_SIZE_NAMES = Object.keys(fontSize);
export const RADIUS_NAMES = Object.keys(radius);
export const LINE_HEIGHT_NAMES = Object.keys(lineHeight);
export const TRACKING_NAMES = Object.keys(letterSpacing);

// Корни палитры, как их пишет класс: `bg-primary` и `bg-primary-tint` — оба `primary`.
export const COLOUR_ROOTS = Object.keys(flatColors);
