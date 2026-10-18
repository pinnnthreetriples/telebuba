import type { ReactNode } from 'react';

// A phone drawn as a phone: a dark body, the front camera, the screen with whatever
// the farm puts on it, the maker on the chin. The screen keeps a phone's tall shape
// whatever the grid column does, so the farm reads as a row of handsets.
const SCREEN_SHAPE = { aspectRatio: '9 / 16' } as const;

export function PhoneMockup({ brand, children }: { brand: string; children: ReactNode }) {
  return (
    <div className="flex w-full flex-col items-center gap-2 rounded-lg border border-line-strong bg-term p-2 shadow-pop">
      <span aria-hidden="true" className="size-dot rounded-full bg-line-strong" />
      <div
        style={SCREEN_SHAPE}
        className="flex w-full flex-col gap-2 overflow-hidden rounded-md bg-surface-card p-3"
      >
        {children}
      </div>
      <span className="type-small text-on-fill">{brand}</span>
    </div>
  );
}
