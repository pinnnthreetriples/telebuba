import type { ReactNode } from 'react';

export function NumberedStep({ number, children }: { number: number; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-px flex size-glyph shrink-0 items-center justify-center rounded-full bg-action-primary text-small font-medium text-on-action">
        {number}
      </span>
      <span className="type-body text-content-subtle">{children}</span>
    </div>
  );
}
