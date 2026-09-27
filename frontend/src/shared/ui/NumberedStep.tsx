import type { ReactNode } from 'react';

export function NumberedStep({ number, children }: { number: number; children: ReactNode }) {
  return (
    <div className="flex items-start gap-md">
      <span className="mt-px flex size-glyph shrink-0 items-center justify-center rounded-full bg-action-primary text-tiny font-semibold text-on-action">
        {number}
      </span>
      <span className="type-prose">{children}</span>
    </div>
  );
}
