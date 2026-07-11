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
      <span
        className="pointer-events-none absolute left-1/2 top-[22px] z-20 hidden w-[230px] -translate-x-1/2 rounded-[10px] border border-line bg-white p-[10px] text-left text-[11.5px] leading-snug text-ink-muted shadow-[0_6px_20px_rgba(0,0,0,0.12)] group-hover:block group-focus-within:block"
        role="tooltip"
      >
        {text}
        {example ? <span className="mt-[6px] block text-ink-subtle">{example}</span> : null}
      </span>
    </span>
  );
}
