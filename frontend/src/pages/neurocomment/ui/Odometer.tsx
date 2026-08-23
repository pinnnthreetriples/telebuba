import { useEffect, useState } from 'react';

// The design's stat odometer: each digit is a 0–9 column that rolls into place
// (translateY, .9s cubic-bezier(.16,1,.3,1)) shortly after the screen mounts —
// the reference's count-up. Matches Telebuba.dc.html L732-736.
// `tone` is a Tailwind text-colour class, not a hex: the stat palette comes from the
// same tokens as everything else.
export function Odometer({ value, tone }: { value: number; tone: string }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => {
      setArmed(true);
    }, 80);
    return () => {
      window.clearTimeout(id);
    };
  }, []);
  return (
    <div
      className={`inline-flex h-[1.1em] overflow-hidden text-stat font-bold leading-[1.1] tabular-nums ${tone}`}
    >
      {String(value)
        .split('')
        .map((ch, index) => (
          <span key={index} className="inline-block h-[1.1em] overflow-hidden">
            <span
              className="flex flex-col transition-transform duration-[900ms] [transition-timing-function:cubic-bezier(.16,1,.3,1)]"
              style={{ transform: `translateY(${(armed ? -(Number(ch) * 1.1) : 0).toFixed(2)}em)` }}
            >
              {Array.from({ length: 10 }, (_, digit) => (
                <span key={digit} className="h-[1.1em] leading-[1.1em]">
                  {digit}
                </span>
              ))}
            </span>
          </span>
        ))}
    </div>
  );
}
