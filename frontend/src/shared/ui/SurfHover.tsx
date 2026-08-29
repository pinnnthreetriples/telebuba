import { useLayoutEffect, useRef, useState } from 'react';

import { cn } from '@/shared/lib/cn';

// Slide-in action layer: the surface translates left to reveal the pinned action
// buttons (the design's lsnSnap/campSnap GSAP, done with CSS). Reveals on hover, when
// `open` is true — a gear button drives `open` so the actions are reachable on touch —
// and when the KEYBOARD reaches an action.
//
// That third case was missing, and it was the sharp end: the actions are always
// rendered and only ever hidden by being COVERED, so they were in the tab order the
// whole time. Tab moved focus onto a button nobody could see, and the focus ring was
// drawn underneath the surface. «Фокусируемое и невидимое» — хуже, чем недостижимое: у
// первого пользователь не знает, где он.
//
// Раскрытие по фокусу сделано на состоянии, а не вариантом `group-focus-within`, и это
// вынужденно: сама поверхность теперь тоже фокусируемая (настоящая кнопка выбора внутри),
// поэтому `group-focus-within` открывал бы действия и при фокусе на карточке. CSS не
// умеет спросить «фокус внутри вот ЭТОГО ребёнка» так, чтобы ответ применился к
// соседнему; `onFocus`/`onBlur` на обёртке действий умеет, потому что в React они
// всплывают.
//
// In shared/ui and not beside the first card that grew it: two screens already
// revealed actions this way, with two different action widths (48px and 52px) and
// therefore two different shifts.
//
// The shift is MEASURED now, and the `shift` prop is gone. It was a number the caller
// worked out, and both callers had worked out the wrong one: the app's rows pass three
// buttons of `w-action` (46px) each — 138px — and asked for 144, so the reveal exposed a
// 6px strip of the action layer's own `bg-canvas` past the last button. The catalog asked
// for 92 against two 22px chips. The old note above claimed «`shift` is always 48 × the
// number of actions», which was true of neither call site, and the test beside this file
// asserted the number the caller passed rather than the distance the actions occupy — so
// nothing anywhere was checking the thing the prop existed to get right.
//
// A component that can measure what it must reveal has no business asking.
export function SurfHover({
  actions,
  surface,
  surfaceId,
  open = false,
}: {
  actions: React.ReactNode;
  surface: React.ReactNode;
  surfaceId?: string;
  open?: boolean;
}) {
  const actionsRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [reached, setReached] = useState(false);

  // Layout effect: the width is wanted before the first paint, or the first hover would
  // travel a stale distance. Measured on the INNER wrapper, not on the action layer: the
  // layer is `inset-x-0` because its `bg-canvas` has to cover the whole row, so its own
  // width is the row's.
  //
  // Записывается прямо в стиль узла, а НЕ через состояние, и это не микрооптимизация.
  // Через состояние измерение вызывает второй рендер сразу после монтирования, а у
  // поверхности стоит `will-change: transform`, то есть она живёт на своём композитном
  // слое: слой переезжает, и текст на нём перерастеризуется чуть иначе. Визуальный гейт
  // это увидел — 238 пикселей разницы на экране нейрокомментинга, устойчиво и при
  // сравнении эталона С САМИМ СОБОЙ. Ширина действий не участвует ни в одном решении
  // рендера, поэтому состоянием ей быть незачем: это стилевой побочный эффект.
  useLayoutEffect(() => {
    const el = actionsRef.current;
    if (!el) return;
    const measure = () => {
      surfaceRef.current?.style.setProperty('--shift', `${String(el.offsetWidth)}px`);
    };
    measure();
    // Guarded like `useWideContainer`'s: the test DOM has no ResizeObserver, and it
    // reports every box as 0×0 anyway.
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => {
      observer.disconnect();
    };
  }, []);
  return (
    // The clip exists only to hide the surface as it slides LEFT, but `overflow`
    // clips every side, and the surface's box is this box: the caller's 1px bottom
    // border landed exactly on the clip boundary and was rasterized away, so the
    // selected campaign card read as an open-bottomed frame. 2px of padding drops
    // the boundary clear of it; the negative margin gives the 2px straight back, so
    // nothing below moves. Measured: clip 2px under the card, card and actions
    // unmoved. A 5px border survived where a 1px one did not, which is what says
    // this is the boundary and not a missing border.
    <div className="group relative -mb-hair overflow-hidden rounded-lg pb-hair">
      {/* `bottom-[2px]`, not `inset-0`: the padding above is behind the card, and an
          action layer stretched into it would show a grey sliver under every row. */}
      <div className="absolute inset-x-0 bottom-[2px] top-0 flex items-stretch justify-end rounded-lg bg-canvas">
        <div
          ref={actionsRef}
          data-measured="actions"
          className="flex items-stretch"
          onFocus={() => {
            setReached(true);
          }}
          onBlur={() => {
            setReached(false);
          }}
        >
          {actions}
        </div>
      </div>
      {/* `bg-surface-card` on the surface, not just on what the caller puts inside it: the
          actions above are always rendered and only ever hidden by being covered, so
          a caller with a translucent surface leaks them. The selected campaign card
          was exactly that — a hand-rolled `bg-action-primary/…` at 6% over white, through
          which pause/edit/delete showed on an unhovered card. It is `bg-info-tint`
          now and opaque on its own, so this backstop has no wearer that needs it and
          stays for the next caller that does: nothing in the class list a caller
          passes can be relied on to be opaque. */}
      <div
        ref={surfaceRef}
        id={surfaceId}
        className={cn(
          'relative rounded-lg bg-surface-card transition-transform duration-reveal ease-out [will-change:transform] group-hover:-translate-x-[var(--shift)]',
          (open || reached) && '-translate-x-[var(--shift)]',
        )}
        // `--shift` ставит эффект выше; до первого замера сдвига нет, и это правильный
        // порядок: раскрыть нечего, пока не известно, на сколько.
        style={{ ['--shift' as string]: '0px' }}
      >
        {surface}
      </div>
    </div>
  );
}
