import { useId, type ReactNode } from 'react';

import { cn } from '@/shared/lib/cn';

// One row of mutually exclusive options, exactly one of them filled. Fourteen sites
// drew this by hand — five through `account-edit`'s local `seg()` helper, nine as
// their own class strings — and none of them had a keyboard: every option was its own
// tab stop, so a four-way log filter cost four Tabs to walk past and the arrow keys
// did nothing. Three of the fourteen claimed `role="radio"` anyway, which promises the
// arrow keys a radiogroup does not have here; the rest split between `aria-pressed`
// (two honest toggles) and nothing at all.
//
// ONE component rather than two, and the wearers are the argument: a tray with a
// raised white segment and a row of outlined chips look like different controls but
// have the same contract — `value`, `onChange`, a fixed option list, exactly one
// active, one disabled flag for the group. The difference between them is a fill, and
// two of the outlined wearers (the captcha provider picker, the warming day presets)
// stand in the same slot a tray stands in: a full-width row of equal options inside a
// card. Splitting on the fill would mean two copies of the roving tabindex below,
// which is the duplication this component exists to end.
//
// It is a RADIOGROUP and only a radiogroup. Tabs that switch a view want
// `role="tablist"`, `aria-selected` and an `aria-controls` pointing at a
// `role="tabpanel"` the CALLER has to render — and the app has exactly one of those
// (ProfileModal's six-tab strip, which is also the only correct keyboard
// implementation in the tree and paints itself with an underline rather than a fill).
// Every one of the fourteen sites here sets a value its surrounding form reads back.
// A `tablist` prop would have zero wearers, so there is none: what that trades away is
// that the session-import switch — the one wearer that does swap a panel — is
// announced as a radio group, and its two panels are not linked by `aria-controls`.
const WRAP = {
  // The inset tray: a sunken grey groove the options sit in, active one raised out of
  // it. Six wearers, all of them a full-width row inside a modal or a card section.
  tray: 'flex gap-tight rounded-lg bg-canvas p-xs',
  // The same idea drawn as a stadium and sized by its labels, for the trays that sit
  // inline at the end of a row rather than spanning it. Its active segment is filled
  // blue instead of raised white — `shadow-pill` is the token for exactly that, "the
  // sliding pill of a segmented tab strip".
  pill: 'inline-flex rounded-full border border-line bg-white p-xs',
  // No tray at all: each option is its own outlined box, and the active one is tinted
  // rather than lifted. Five wearers.
  outline: 'flex gap-sm',
} as const;

const SEG = {
  tray: 'flex-1 rounded-sm py-sm text-body font-medium',
  pill: 'rounded-full px-lg py-tight text-body font-medium',
  outline: 'flex-1 rounded-lg border px-md py-sm text-body font-medium',
} as const;

const ON = {
  tray: 'bg-white text-ink shadow-seg',
  pill: 'bg-primary text-white shadow-pill',
  outline: 'border-primary bg-primary-tint text-primary-deep',
} as const;

const OFF = {
  tray: 'text-ink-muted',
  pill: 'text-ink-muted',
  outline: 'border-line bg-white text-ink-muted hover:border-line-strong hover:bg-surface',
} as const;

// `group relative` is for the one wearer whose option carries a HintBubble (the
// neurocomment mode pair): the bubble is positioned against, and revealed by, the
// nearest `group`, and with an option list the segment itself is the only element the
// caller can hang it on. Both utilities paint nothing on their own.
//
// `focus-visible:shadow-focus` is the state none of the fourteen had — the same recipe
// `Button` and `Select`'s trigger wear, so a segment reached by Tab or by an arrow key
// says so the way every other control in the app does.
const BASE =
  'group relative transition-colors duration-state focus-visible:shadow-focus focus-visible:outline-none disabled:opacity-60';

export type SegmentedOption<T extends string> = {
  value: T;
  label: ReactNode;
  // The native tooltip. Two wearers need it: the warming scope switch explains why
  // "this account only" is the safe pick, and the neurocomment mode pair uses it as
  // the touch/screen-reader fallback for its hover bubble.
  title?: string;
  // An accessible name that is not the visible label. The privacy rows need it: three
  // rows of «Все»/«Контакты»/«Никто» put nine identically named buttons in one element
  // list, so each option's name has to carry the row it belongs to.
  ariaLabel?: string;
};

export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  variant = 'tray',
  disabled = false,
  ariaLabel,
  className,
}: {
  // A plain `string`, not `T`: the privacy rows hold a fourth state ('unknown') that
  // is not one of the three options and must press none of them.
  value: string;
  onChange: (value: T) => void;
  options: readonly SegmentedOption<T>[];
  variant?: keyof typeof WRAP;
  // The whole group at once. Deliberately not per-option: a segmented control whose
  // options can be individually switched off is a list with holes in it, and the one
  // site in the app that wants that (the run-mode picker, whose second option the
  // server refuses) draws two description cards rather than a tray.
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
}) {
  const groupId = useId();
  const optionId = (index: number) => `${groupId}-${String(index)}`;
  // Where the tab stop sits. `value` is allowed to name nothing in the list — the
  // privacy rows report an 'unknown' level Telegram holds and this app does not model,
  // and that must press no option — so the group falls back to its first option rather
  // than becoming unreachable by keyboard.
  const checked = options.findIndex((option) => option.value === value);
  const stop = checked === -1 ? 0 : checked;

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(WRAP[variant], className)}
      // A radiogroup is ONE tab stop: Tab lands on the checked option and Tab again
      // leaves the group, while the arrows move within it. Selection follows focus,
      // which is what the ARIA radio pattern asks for and what ProfileModal's tablist
      // already does — so the arrow keys commit the same `onChange` a click would, and
      // focus has to follow the selection to keep the roving stop under the cursor.
      // Both axes are accepted: the trays are horizontal rows, but an operator who
      // reaches for Down on a two-option switch means the next one.
      onKeyDown={(event) => {
        const { key } = event;
        const last = options.length - 1;
        let next: number;
        if (key === 'ArrowRight' || key === 'ArrowDown') next = stop === last ? 0 : stop + 1;
        else if (key === 'ArrowLeft' || key === 'ArrowUp') next = stop === 0 ? last : stop - 1;
        else if (key === 'Home') next = 0;
        else if (key === 'End') next = last;
        else return;
        const chosen = options[next];
        if (!chosen) return;
        event.preventDefault();
        onChange(chosen.value);
        document.getElementById(optionId(next))?.focus();
      }}
    >
      {options.map((option, index) => (
        <button
          key={option.value}
          id={optionId(index)}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          aria-label={option.ariaLabel}
          title={option.title}
          tabIndex={index === stop ? 0 : -1}
          disabled={disabled}
          onClick={() => {
            onChange(option.value);
          }}
          className={cn(BASE, SEG[variant], option.value === value ? ON[variant] : OFF[variant])}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
