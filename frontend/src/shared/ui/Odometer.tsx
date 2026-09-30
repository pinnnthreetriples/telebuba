import { useEffect, useState } from 'react';

import { cn } from '@/shared/lib/cn';

function DigitColumns({ digits }: { digits: string }) {
  const [settled, setSettled] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false,
  );

  useEffect(() => {
    if (settled) return;
    // Paint the zero position once before changing the transform. One frame can be
    // coalesced with the mount when React runs an interaction effect before paint.
    let secondFrame: number | undefined;
    const firstFrame = window.requestAnimationFrame(() => {
      secondFrame = window.requestAnimationFrame(() => setSettled(true));
    });
    return () => {
      window.cancelAnimationFrame(firstFrame);
      if (secondFrame !== undefined) window.cancelAnimationFrame(secondFrame);
    };
  }, [settled]);

  return (
    <span aria-hidden="true" className="inline-flex">
      {digits.split('').map((digit, index) => (
        <span key={index} className="inline-block h-[1.1em] overflow-hidden">
          <span
            className="flex flex-col transition-transform duration-roll ease-out"
            style={{
              transform: `translateY(${(settled ? -Number(digit) * 1.1 : 0).toFixed(2)}em)`,
            }}
          >
            {Array.from({ length: 10 }, (_, entry) => (
              <span key={entry} className="h-[1.1em] leading-[1.1em]">
                {entry}
              </span>
            ))}
          </span>
        </span>
      ))}
    </span>
  );
}

/** Animated non-negative integer for stat tiles. Each digit rolls within a fixed-height clip. */
export function Odometer({ value, className }: { value: number; className?: string }) {
  const digits = String(value);

  return (
    <span
      className={cn(
        'inline-flex h-[1.1em] overflow-hidden type-stat leading-[1.1em] tabular-nums transition-[width] duration-roll ease-out',
        className,
      )}
      style={{ width: `${digits.length}ch` }}
    >
      <span className="sr-only">{value}</span>
      {/* A new decimal place starts a fresh forward roll; same-width updates retain their columns. */}
      <DigitColumns key={digits.length} digits={digits} />
    </span>
  );
}
