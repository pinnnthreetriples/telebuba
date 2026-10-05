import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

import {
  FONT_SIZE_NAMES,
  FONT_WEIGHT_NAMES,
  LINE_HEIGHT_NAMES,
  RADIUS_NAMES,
  RHYTHM_NAMES,
  TRACKING_NAMES,
  TYPE_ROLE_NAMES,
} from '@/shared/design-system/tokens/names';
import { pressScale } from '@/shared/design-system/tokens/motion';

// The shadcn/ui class-merge helper: clsx for conditional joins, tailwind-merge to
// dedupe conflicting Tailwind utilities (last one wins).
//
// tailwind-merge carries Tailwind's DEFAULT scales, and this config replaces two of
// them outright, so it has to be told the new names. Without that it cannot tell a
// type rung from a text colour — both are spelled `text-*` — and resolves
// `text-body text-white` to `text-white`, silently dropping the size. That is not
// hypothetical: it is what `Button` produced, since its variant paints the colour
// after its size sets the rung.
//
// `font-size` and `border-radius` are the two groups the config replaces with names
// tailwind-merge has never seen. The other replaced scales (gap, duration, z-index,
// shadow) share their prefix with nothing else, so an unknown value there conflicts
// with the right group by prefix alone.
//
// `leading` and `tracking` are a third case, and the reason they are listed is the
// opposite of the `text-*` one: tailwind-merge DOES know both prefixes, but it matches
// them against Tailwind's own names plus a length or an arbitrary value, and
// ``, `` and `tracking-code` are none of those. An unrecognised
// class joins no group, so it conflicts with nothing and `cn('',
// 'leading-none')` keeps BOTH — the winner then decided by the order the two rules
// happen to sit in the stylesheet rather than by the caller's last word. That is the
// same shape as the bug that switched off every filled Button's font size, one axis
// over, and it fails silently in exactly the same way.
// The `type-*` roles are a third group, and they cannot join `font-size`: a role sets a
// size, a weight AND a colour, so folding it into the size group would let a later
// `text-body` delete all three and leave the text unweighted and unpainted — the same
// shape of bug as the one above, one layer up. It gets its own group instead, declared
// to beat the three groups it subsumes when it comes last, and NOT declared as
// something they beat: `cn('type-small', 'text-danger')` has to keep both, because
// naming the role and then recolouring it is the intended way to write an error line.
//
// The `override` below is the other half of that, and it is a conflict tailwind-merge
// is right about everywhere except here. Stock Tailwind's `fontSize` entries are
// [size, line-height] tuples, so `text-lg` really does set a line-height and really
// does have to clear the `leading-*` written before it. This config's rungs are bare
// strings for exactly the opposite reason — the note above `fontSize` says pairing a
// line-height into them would silently re-space 694 sites — so `text-*` here sets a
// size and nothing else, and letting it clear a line-height drops a class the caller
// asked for: `cn('', 'text-small')` returned `text-small` alone. That is
// the same silent-drop shape as the Button bug, arriving from the other direction, and
// it was reachable before this axis had names at all, because an arbitrary
// `leading-[1.5]` lands in the same group a named rung does.
// The rhythm was the same case as `leading`/`tracking`, and the widest one: while the
// config named its rungs (`tight`, `md`, `2xl`), tailwind-merge validated a padding,
// margin or gap value with `isLength` — which those names are not — so they joined no
// group and `cn('py-tight', 'py-xs')` kept BOTH, letting a component's own padding beat
// its caller's override by stylesheet order. Since the Firecrawl pass the rungs are
// numeric keys (`1`…`16`) that stock tailwind-merge parses by itself; the rhythm groups
// below stay declared from the token list anyway, so a named rung added later cannot
// reopen the hole silently.
//
// Все четыре списка ниже ВЫЧИСЛЯЮТСЯ из `shared/design-system/tokens`, а не набраны здесь.
// Набранные, они были шестым экземпляром состава шкал — и самым опасным: расходясь, они
// не красят ничего неправильно, а перестают разрешать конфликт, и перекрытие вызывающего
// молча выбрасывается. Опасен именно этот экземпляр потому, что расхождение возникает не
// при переименовании (его бы заметили), а при ДОБАВЛЕНИИ ступени: новый рунг появляется в
// токенах, список не растёт, и tailwind-merge про рунг просто не знает.
//
// Импорт токенов, а не конфига Tailwind: конфиг тянет `tailwindcss/plugin`, и это код
// приложения — плагин уехал бы в браузерный бандл. Модули токенов не импортируют ничего.
//
// И импорт ГЛУБОКИЙ, `tokens/names`, а не через `@/shared/design-system`. Через баррель
// получался цикл: `cn.ts` → баррель → `recipes/*` → `cn.ts`. Vite такой цикл разрешает
// молча, поэтому он и прожил до ревью, но молчание тут — свойство сборщика, а не кода:
// в цикле порядок инициализации модулей зависит от того, кто вошёл первым, и `RHYTHM_NAMES`
// имеет право оказаться `undefined` у того, кто вошёл вторым. `tokens/` не импортирует
// ничего, поэтому глубокий путь цикла не образует ни в какую сторону.
const RHYTHM = RHYTHM_NAMES;
const PRESS_SCALE = Object.keys(pressScale);

const merge = extendTailwindMerge<'type-role'>({
  override: {
    conflictingClassGroups: { 'font-size': [] },
  },
  extend: {
    classGroups: {
      'font-size': [{ text: FONT_SIZE_NAMES }],
      'font-weight': [{ font: FONT_WEIGHT_NAMES }],
      // Шкала целиком, а не только незнакомые tailwind-merge имена. Отбирать
      // незнакомые пришлось бы по СТОКОВОМУ словарю (сейчас он знает все пять имён, но
      // знал не все), то есть завести здесь литеральный список чужих имён — ровно та
      // копия, от которой этот файл только что избавился. Повторное объявление имени,
      // которое и так в этой группе, — пустая операция: соответствие «класс → группа»
      // просто переписывается на ту же группу.
      rounded: [{ rounded: RADIUS_NAMES }],
      leading: [{ leading: LINE_HEIGHT_NAMES }],
      tracking: [{ tracking: TRACKING_NAMES }],
      // One entry per utility tailwind-merge resolves separately: `p` conflicts with
      // `px`/`py` and each of those with its own two sides, and that whole lattice is
      // already declared in the stock config — it is only the VALUES it does not
      // recognise. Naming them here restores the lattice rather than rebuilding it.
      p: [{ p: RHYTHM }],
      px: [{ px: RHYTHM }],
      py: [{ py: RHYTHM }],
      pt: [{ pt: RHYTHM }],
      pr: [{ pr: RHYTHM }],
      pb: [{ pb: RHYTHM }],
      pl: [{ pl: RHYTHM }],
      m: [{ m: RHYTHM }],
      mx: [{ mx: RHYTHM }],
      my: [{ my: RHYTHM }],
      mt: [{ mt: RHYTHM }],
      mr: [{ mr: RHYTHM }],
      mb: [{ mb: RHYTHM }],
      ml: [{ ml: RHYTHM }],
      gap: [{ gap: RHYTHM }],
      'gap-x': [{ 'gap-x': RHYTHM }],
      'gap-y': [{ 'gap-y': RHYTHM }],
      'space-x': [{ 'space-x': RHYTHM }],
      'space-y': [{ 'space-y': RHYTHM }],
      scale: [{ scale: PRESS_SCALE }],
      'type-role': [{ type: TYPE_ROLE_NAMES }],
    },
    conflictingClassGroups: {
      'type-role': ['font-size', 'font-weight', 'text-color'],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}
