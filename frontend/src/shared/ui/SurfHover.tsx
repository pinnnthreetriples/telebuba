// Slide-in action layer: the surface translates left to reveal the pinned action
// buttons (the design's lsnSnap/campSnap GSAP, done with CSS). Reveals on hover
// AND when `open` is true — a gear button drives `open` so the actions are
// reachable on touch/keyboard, not hover-only (finding #6).
//
// In shared/ui and not beside the first card that grew it: two screens already
// revealed actions this way, with two different action widths (48px and 52px) and
// therefore two different shifts. One action is 48px wide everywhere now, so
// `shift` is always 48 × the number of actions.
export function SurfHover({
  actions,
  surface,
  shift,
  surfaceId,
  open = false,
}: {
  actions: React.ReactNode;
  surface: React.ReactNode;
  shift: number;
  surfaceId?: string;
  open?: boolean;
}) {
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
        {actions}
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
        id={surfaceId}
        className={`relative rounded-lg bg-surface-card transition-transform duration-reveal ease-out [will-change:transform] group-hover:-translate-x-[var(--shift)] ${open ? '-translate-x-[var(--shift)]' : ''}`}
        style={{ ['--shift' as string]: `${String(shift)}px` }}
      >
        {surface}
      </div>
    </div>
  );
}
