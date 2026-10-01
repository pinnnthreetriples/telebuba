import { spinnerGeometry } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// Кольцо ожидания. Оно было написано руками семнадцать раз, и это не про повторение
// строки, а про то, что повторение строки разошлось:
//
//   `border-line border-t-action-primary`         7 сайтов
//   `border-line-strong border-t-action-primary`  5 сайтов
//
// Дорожка кольца была двумя разными серыми в одной работе, и различить их можно только
// рядом. Здесь она одна — `line-strong`: на 2px дуги волосяной серый на белом почти не
// читается, и кольцо превращается в одинокую синюю запятую. Семь сайтов из первой строки
// поэтому стали на шаг темнее, и это единственное намеренное изменение картинки в этой
// правке.
//
// Размеров три, и до этого их было СЕМЬ: локальный `Spinner` в `account-edit` брал размер
// числом (`size={12}`, `13`, `14`, `15`) и ставил его инлайновым стилем, а разметка вокруг
// носила `size-spinner`, `size-chip` и `size-tile`. Четыре числа в пределах трёх пикселей
// — это не решение, а место, где легла рука; ровно тот же дефект, что `backdrop?: number`
// у диалога. Все четыре стали `sm`.
// The large ring alone needs a 3px stroke: a ring-specific optical correction.
const STROKE = { sm: 'border-2', md: 'border-2', lg: 'border-[3px]' } as const;

// Тон говорит, ПО ЧЕМУ кольцо крутится, а не какого оно цвета: `onAction` — на залитом
// действии, где синий на синем не виден; `danger` — в подтверждении необратимого
// действия, где кольцо продолжает его красную линию.
//
// Тон звался `inverse` и красился `border-white/40 border-t-white`, то есть обходил
// уровень назначения дважды: имя обещало «на любой тёмной поверхности», а все четыре
// носителя стоят внутри `Button variant="primary"` — это не абстрактный inverse, а именно
// чернила на действии; и белый с альфой — краска, которую палитра не видит, притом что
// под ней всегда `blue600` и композит существует. Теперь и дуга, и дорожка — роли.
const TONE = {
  default: 'border-line-strong border-t-action-primary',
  onAction: 'border-on-action-track border-t-on-action',
  danger: 'border-danger-line border-t-danger',
} as const;

// Экспортируется ради `Button`: он выбирает тон кольца по своей заливке, и связь между
// двумя наборами держит компилятор, а не совпадение строк.
export type SpinnerTone = keyof typeof TONE;

/**
 * Крутящееся кольцо. `aria-hidden`, и это сохранение поведения, а не решение: все
 * семнадцать рукописных колец были пустыми `<span>` без роли, то есть для скринридера не
 * существовали и раньше. Роль `status` здесь сделала бы хуже — кольца стоят внутри
 * кнопок, которые опрашивают сервер, и каждая живая область объявляла бы себя на каждом
 * опросе. Состояние ожидания сообщает КОНТРОЛ (`disabled`, подпись), а кольцо его рисует.
 */
export function Spinner({
  size = 'sm',
  tone = 'default',
  className = '',
}: {
  size?: keyof typeof STROKE;
  tone?: SpinnerTone;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(spinnerGeometry(size), STROKE[size], TONE[tone], className)}
    />
  );
}
