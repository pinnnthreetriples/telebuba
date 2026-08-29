import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { buttonBase, type ControlSize } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

import { Spinner, type SpinnerTone } from './Spinner';

// The app's text buttons, as the three shapes the design actually has and the four
// fills it paints them with. Before this they were 98 hand-written class strings
// across 72 distinct spellings, and the differences were rarely intentional: the
// same "cancel" button was `disabled:opacity-50` in one dialog and
// `disabled:opacity-60` in the next, hover was set on one button in sixty-six, and
// `focus-visible` appeared three times in the whole app.
//
// `IconButton` остаётся своим компонентом, а не ступенью здесь: он квадратный, не несёт
// текста, и его доступное имя приходит из `aria-label` — кнопка с обязательной подписью
// это другой контракт, а не вариант.
//
// Высота, горизонтальные поля, рунг размера, форма, фокус, disabled и переход приходят из
// `recipes/controls.ts` — того же рецепта, что у Input, Textarea и Select. Раньше каждый
// набирал это сам, и высота СКЛАДЫВАЛАСЬ из padding и интерлиньяжа: `size="md"` значил
// ~40px у кнопки и ~41px у поля, а смена рунга размера меняла высоту контрола. Теперь
// высота фиксированная и общая — `Button size="sm"` и `Input size="sm"` ровно одинаковы.
//
// Здесь осталось то, что действительно принадлежит кнопке: заливка (`VARIANT`) и вес
// надписи. Чем кнопка залита — её собственное решение; какого она размера — общее.
//
// `md` — подвал диалога и действие уровня страницы; `sm` — действие внутри карточки, где
// `md` задал бы высоту шапки; `xs` — то, что стоит в строке таблицы рядом со значением;
// `block` — действие, которое растягивается на всю форму, стоя последней строкой под
// полями, которые оно подтверждает.
//
// Промежуточной ступени между `sm` и `xs` нет, хотя семь кнопок её просили, и пятой для
// шести кнопок пагинации тоже нет: и те и другие стоят внутри карточки, а это предложение,
// на которое уже отвечает `sm`. С фиксированными высотами этот спор закрыт окончательно —
// ступеней ровно столько, сколько высот, а высот четыре.
//
// ── Ступень НЕ решает форму ────────────────────────────────────────────────────────
//
// Решала: `md`/`sm` были пилюлями, `xs` — `rounded-md`, `block` — `rounded-lg`. То есть
// «сделать кнопку меньше» означало «сделать кнопку другой формы», и у обоих исключений
// было написанное здесь объяснение. Оба не выдержали счёта: «на 28px полный радиус и
// прямоугольник — одна форма» неверно (14px против 8px радиуса видно рядом), а «пилюля на
// 200px была бы стадионом» — это про вкус, против которого стояли 83 пилюли из 104 кнопок.
//
// Форма теперь одна и живёт в `buttonBase`, где её нельзя подменить ни отсюда, ни из места
// вызова. Ступень отвечает за высоту и поля; здесь к ней остался вес надписи — и ширина у
// `block`, единственной ступени, у которой она своя.
//
// `block` — единственная не `inline-flex`: `w-full` на строчном боксе всё равно стоит на
// строке и собирает под собой её интерлиньяж. Ширина — не форма, поэтому она осталась.
const SIZE: Record<
  'md' | 'sm' | 'xs' | 'block',
  { size: ControlSize; weight: string; extra?: string }
> = {
  md: { size: 'md', weight: 'font-semibold' },
  sm: { size: 'sm', weight: 'font-semibold' },
  xs: { size: 'xs', weight: 'font-medium' },
  block: { size: 'md', weight: 'font-medium', extra: 'flex w-full' },
};

// `primary` is the one committing action on a screen and `secondary` everything
// beside it; `danger` is the committing action when that action destroys something
// (it is a tinted button, not a red one — the red is the label); `ghost` has no box
// until you point at it; `dashed` adds one more of whatever the list above it holds,
// drawn as the empty slot the new thing will fill.
//
// `dashed` is a fill and not a shape, which is why it is here rather than in `SIZE`:
// its three wearers are all `block`, but `block` is worn by three different fills, so
// the two do not travel together.
//
// `dashedMuted` — второй пунктирный, и он заливка, а не «или» внутри первого: синий стоит
// БЛОКОМ под списком и приглашает добавить в него, приглушённый стоит В СТРОКЕ чипов рядом
// с ними. Здесь он потому, что порог этого файла — «имя даётся заливке, когда её просят два
// независимых слайса» — выполнен: страница прогрева и нейрокомментинг набирали её дословно
// одной и той же строкой. Прежний комментарий назначал себе более высокий порог («если
// появится третий») и на этом оставлял две кнопки рукописными, то есть вне системы формы.
//
// There is no white-filled `danger`, though five buttons wear one: the retry beside
// the error sentence inside a `Notice tone="danger"`, where this variant's own
// `bg-danger-tint` is the tint it is already standing on. The white is a real
// decision, so those five say it as `className="bg-surface-card"` instead of losing it — but
// all five live in `widgets/account-edit`, and a fill is not given a name until two
// independent slices ask for it.
const VARIANT = {
  primary: 'bg-action-primary text-on-action hover:bg-action-pressed',
  secondary: 'border border-line bg-surface-card text-content-primary hover:border-line-strong',
  danger: 'border border-danger-line bg-danger-tint text-danger-deep hover:border-danger',
  ghost: 'text-content-muted hover:bg-canvas hover:text-content-primary',
  dashed:
    'border border-dashed border-info-line bg-surface-card text-info-strong hover:border-action-primary hover:bg-action-hover',
  dashedMuted:
    'border border-dashed border-line-strong bg-surface-card text-content-muted hover:border-action-primary hover:text-action-primary',
} as const;

// Тон кольца ожидания — следствие заливки, а не второе решение вызывающего. `satisfies`, а
// не аннотация: новая заливка без своего тона не компилируется.
const SPINNER_TONE = {
  primary: 'onAction',
  secondary: 'default',
  danger: 'danger',
  ghost: 'default',
  dashed: 'default',
  dashedMuted: 'default',
} satisfies Record<keyof typeof VARIANT, SpinnerTone>;

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  disabled = false,
  className,
  children,
  ...rest
}: {
  variant?: keyof typeof VARIANT;
  size?: keyof typeof SIZE;
  // Запрос в полёте — ОДНО состояние: кольцо перед содержимым, подпись на месте, клики не
  // проходят, `aria-busy` объявлен. Отдельным пропом от `disabled`, потому что скринридер
  // должен слышать разницу между «занято» и «выключено».
  //
  // Подпись — забота вызывающего: «Сохраняю…» вместо «Сохранить» это текст, а не
  // состояние. Кольцо не подменяет её: доступное имя кнопки есть её содержимое, и кнопка,
  // отдавшая подпись кольцу, на время запроса становится безымянной. Историю тринадцати
  // рукописных сборок держит docs/design-system.md, гейт — `Button.test.tsx`.
  loading?: boolean;
  children?: ReactNode;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> & { className?: string }) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        buttonBase({ size: SIZE[size].size }),
        'aria-busy:cursor-progress',
        // Кольцу нужно больше воздуха, чем глифу: базовый зазор кнопки — `tight` (6px),
        // и все тринадцать рукописных обёрток вокруг кольца ставили `gap-sm` (8px). Это
        // решение, а не подгонка под прежнюю картинку, и оно живёт только на время
        // ожидания — обычный зазор кнопки не меняется.
        loading && 'gap-sm',
        SIZE[size].weight,
        SIZE[size].extra,
        VARIANT[variant],
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner tone={SPINNER_TONE[variant]} /> : null}
      {children}
    </button>
  );
}
