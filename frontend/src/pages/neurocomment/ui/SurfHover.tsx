// Slide-in action layer: the surface translates left to reveal the pinned action
// buttons (the design's lsnSnap/campSnap GSAP, done with CSS). Reveals on hover
// AND when `open` is true — a gear button drives `open` so the actions are
// reachable on touch/keyboard, not hover-only (finding #6).
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
    <div className="group relative overflow-hidden rounded-[11px]">
      <div className="absolute inset-0 flex items-stretch justify-end rounded-[11px] bg-[#f1efed]">
        {actions}
      </div>
      <div
        id={surfaceId}
        className={`relative rounded-[11px] transition-transform duration-[440ms] [transition-timing-function:cubic-bezier(.16,1,.3,1)] [will-change:transform] group-hover:-translate-x-[var(--shift)] ${open ? '-translate-x-[var(--shift)]' : ''}`}
        style={{ ['--shift' as string]: `${String(shift)}px` }}
      >
        {surface}
      </div>
    </div>
  );
}
