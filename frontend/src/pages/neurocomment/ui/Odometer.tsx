// The stat odometer shows its actual value on first paint. The CSS transition
// still rolls a digit when a live value changes, without replaying a count-up
// every time the user returns to this page.
// `tone` is a Tailwind text-colour class, not a hex: the stat palette comes from the
// same tokens as everything else.
export function Odometer({ value, tone }: { value: number; tone: string }) {
  return (
    <div
      className={`inline-flex h-[1.1em] overflow-hidden type-stat leading-[1.1em] tabular-nums ${tone}`}
    >
      {String(value)
        .split('')
        .map((ch, index) => (
          <span key={index} className="inline-block h-[1.1em] overflow-hidden">
            <span
              className="flex flex-col transition-transform duration-roll ease-out"
              style={{ transform: `translateY(${(-Number(ch) * 1.1).toFixed(2)}em)` }}
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
