import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { buttonBase, type ControlShape, type ControlSize } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

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
// `md` задал бы высоту шапки; `xs` — то, что стоит в строке таблицы рядом со значением, и
// единственная ступень не-пилюля: на 28px полный радиус и прямоугольник — одна форма.
// `block` — действие, которое растягивается на всю форму, стоя последней строкой под
// полями, которые оно подтверждает.
//
// Промежуточной ступени между `sm` и `xs` нет, хотя семь кнопок её просили, и пятой для
// шести кнопок пагинации тоже нет: и те и другие стоят внутри карточки, а это предложение,
// на которое уже отвечает `sm`. С фиксированными высотами этот спор закрыт окончательно —
// ступеней ровно столько, сколько высот, а высот четыре.
//
// Ступень рецепта + форма + вес. Имена ступеней не менялись: их набрано 200+ мест, и
// переименование стоило бы 200 правок, не сказав ничего нового.
//
// `block` — единственная ступень, у которой ширина своя, и единственная не `inline-flex`:
// `w-full` на строчном боксе всё равно стоит на строке и собирает под собой её
// интерлиньяж. Её `rounded-lg` — имя радиуса для панели, вложенной в карточку, то есть
// форма полноширинной строки; пилюля здесь была бы стадионом на 200px.
const SIZE: Record<
  'md' | 'sm' | 'xs' | 'block',
  { size: ControlSize; shape: ControlShape; weight: string; extra?: string }
> = {
  md: { size: 'md', shape: 'pill', weight: 'font-semibold' },
  sm: { size: 'sm', shape: 'pill', weight: 'font-semibold' },
  xs: { size: 'xs', shape: 'inset', weight: 'font-medium' },
  block: { size: 'md', shape: 'field', weight: 'font-medium', extra: 'flex w-full' },
};

// `primary` is the one committing action on a screen and `secondary` everything
// beside it; `danger` is the committing action when that action destroys something
// (it is a tinted button, not a red one — the red is the label); `ghost` has no box
// until you point at it; `dashed` adds one more of whatever the list above it holds,
// drawn as the empty slot the new thing will fill.
//
// `dashed` is a fill and not a shape, which is why it is here rather than in `SIZE`:
// its three wearers are all `block`, but `block` is worn by three different fills, so
// the two do not travel together. There is a SECOND dashed button in the app — the
// muted inline one that opens a channel field (neurocomment's CampaignsCard, the
// warming page) — and it is deliberately not this variant: it is drawn in
// `line-strong` and `ink-muted` rather than in blue, so folding it in would need a
// rung whose purpose could not be said without an "or". Those two stay hand-written.
//
// There is no white-filled `danger`, though five buttons wear one: the retry beside
// the error sentence inside a `Notice tone="danger"`, where this variant's own
// `bg-danger-tint` is the tint it is already standing on. The white is a real
// decision, so those five say it as `className="bg-white"` instead of losing it — but
// all five live in `widgets/account-edit`, and a fill is not given a name until two
// independent slices ask for it.
const VARIANT = {
  primary: 'bg-primary text-white hover:bg-primary-press',
  secondary: 'border border-line bg-white text-ink hover:border-line-strong',
  danger: 'border border-danger-line bg-danger-tint text-danger-deep hover:border-danger',
  ghost: 'text-ink-muted hover:bg-canvas hover:text-ink',
  dashed:
    'border border-dashed border-primary-line bg-white text-primary-deep hover:border-primary hover:bg-primary-tint',
} as const;

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
  // A request is in flight. The button reports it with `aria-busy` and stops taking
  // clicks, and that is all: this app says "Сохраняю…" in the label while it waits,
  // and a spinner next to that sentence would be the same fact told twice. Kept as
  // its own prop rather than folded into `disabled` because a screen reader must
  // hear the difference between "busy" and "off".
  loading?: boolean;
  children?: ReactNode;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> & { className?: string }) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        buttonBase({ size: SIZE[size].size, shape: SIZE[size].shape }),
        'aria-busy:cursor-progress',
        SIZE[size].weight,
        SIZE[size].extra,
        VARIANT[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
