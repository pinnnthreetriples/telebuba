// A small "?" badge that reveals a short plain-language explanation on hover or
// keyboard focus. Pure CSS (group-hover / focus-within) so there's no popover
// library; `title` is the accessible/native fallback. Used next to settings
// labels where the field's effect isn't obvious from its name.
const BADGE =
  'flex h-[15px] w-[15px] shrink-0 cursor-help items-center justify-center rounded-full ' +
  'border border-line-input text-[10px] font-bold leading-none text-ink-subtle ' +
  'transition-colors hover:border-primary hover:text-primary focus:outline-none ' +
  'focus-visible:border-primary focus-visible:text-primary';

export function HelpHint({ text, example }: { text: string; example?: string }) {
  const title = example ? `${text}\n${example}` : text;
  return (
    <span className="group relative inline-flex align-middle">
      <span role="note" aria-label={title} tabIndex={0} title={title} className={BADGE}>
        ?
      </span>
      {/* Below `md` the popover is pinned to the viewport rather than to the badge:
          a 230px box centred on a badge near either screen edge clips, and no fixed
          anchor is safe without measuring. group-focus-within is what opens it on a
          touch device — the badge is tabIndex=0, and hover never fires there. */}
      <span
        className="pointer-events-none fixed inset-x-3 bottom-3 z-20 hidden rounded-[10px] border border-line bg-white p-[10px] text-left text-[11.5px] leading-snug text-ink-muted shadow-[0_6px_20px_rgba(0,0,0,0.12)] group-hover:block group-focus-within:block md:absolute md:inset-x-auto md:bottom-auto md:left-1/2 md:top-[22px] md:w-[230px] md:-translate-x-1/2"
        role="tooltip"
      >
        {text}
        {example ? <span className="mt-[6px] block text-ink-subtle">{example}</span> : null}
      </span>
    </span>
  );
}
