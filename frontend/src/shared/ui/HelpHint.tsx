// A small "?" badge that reveals a short plain-language explanation on hover or
// keyboard focus. Pure CSS (group-hover / focus-within) so there's no popover
// library; `title` is the accessible/native fallback. Used next to settings
// labels where the field's effect isn't obvious from its name.
const BADGE =
  'flex h-[15px] w-[15px] shrink-0 cursor-help items-center justify-center rounded-full ' +
  'border border-line-input text-micro font-bold leading-none text-ink-subtle ' +
  'transition-colors hover:border-primary hover:text-primary focus:outline-none ' +
  'focus-visible:border-primary focus-visible:text-primary';

// The bubble on its own, for the callers whose trigger is the control itself rather than a
// "?" beside it — a control that is already a <button> cannot host the badge, since the
// badge is focusable and would nest one interactive element inside another. Exported so the
// app has ONE tooltip look: the second copy of this class list is where they start drifting.
// Requires `group relative` on the wrapper, which is also what anchors it.
export function HintBubble({ text, example }: { text: string; example?: string }) {
  return (
    /* ponytail: stays trigger-anchored at every width. A 230px box centred on a
       trigger near a screen edge can clip on a phone, but the viewport-pinned
       alternative (fixed inset-x-3 bottom-3 below md) is worse: inside a centred
       modal it paints on the backdrop *below* the dialog, and two hints resolving
       to the same rect can overlap. Fix properly with measurement, not a media
       query, if the clipping ever actually bites. */
    <span
      className="pointer-events-none absolute left-1/2 top-[calc(100%+7px)] z-pop hidden w-[230px] -translate-x-1/2 rounded-lg border border-line bg-white p-[10px] text-left text-tiny leading-snug text-ink-muted shadow-[0_6px_20px_rgba(0,0,0,0.12)] group-hover:block group-focus-within:block"
      role="tooltip"
    >
      {text}
      {example ? <span className="mt-[6px] block text-ink-subtle">{example}</span> : null}
    </span>
  );
}

export function HelpHint({ text, example }: { text: string; example?: string }) {
  const title = example ? `${text}\n${example}` : text;
  return (
    <span className="group relative inline-flex align-middle">
      <span role="note" aria-label={title} tabIndex={0} title={title} className={BADGE}>
        ?
      </span>
      <HintBubble text={text} example={example} />
    </span>
  );
}
