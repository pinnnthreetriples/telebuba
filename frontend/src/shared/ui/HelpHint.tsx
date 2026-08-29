import { surface } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// A small "?" badge that reveals a short plain-language explanation on hover or
// keyboard focus. Pure CSS (group-hover / focus-within) so there's no popover
// library; `title` is the accessible/native fallback. Used next to settings
// labels where the field's effect isn't obvious from its name.
const BADGE =
  'flex size-glyph shrink-0 cursor-help items-center justify-center rounded-full ' +
  'border border-line text-tiny font-bold leading-none text-content-subtle ' +
  'transition-colors hover:border-action-primary hover:text-action-primary focus:outline-none ' +
  'focus-visible:border-focus focus-visible:text-focus';

// The bubble on its own, for the callers whose trigger is the control itself rather than a
// "?" beside it — a control that is already a <button> cannot host the badge, since the
// badge is focusable and would nest one interactive element inside another. Exported so the
// app has ONE tooltip look: the second copy of this class list is where they start drifting.
// Requires `group relative` on the wrapper, which is also what anchors it.
export function HintBubble({ text, example }: { text: string; example?: string }) {
  return (
    /* ponytail: stays trigger-anchored at every width. A 230px box centred on a
       trigger near a screen edge can clip on a phone, but the viewport-pinned
       alternative (fixed inset-x-lg bottom-lg below md) is worse: inside a centred
       modal it paints on the backdrop *below* the dialog, and two hints resolving
       to the same rect can overlap. Fix properly with measurement, not a media
       query, if the clipping ever actually bites. */
    <span
      className={cn(
        'pointer-events-none absolute left-1/2 top-[calc(100%+7px)] z-pop hidden w-tip -translate-x-1/2 p-md text-left text-tiny text-content-muted group-hover:block group-focus-within:block',
        surface('panel'),
      )}
      role="tooltip"
    >
      {text}
      {example ? <span className="mt-tight block text-content-subtle">{example}</span> : null}
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
