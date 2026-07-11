import type { ReactNode } from 'react';

// The design's pill switch (track + sliding thumb), 18px of travel.
export function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => {
        onChange(!checked);
      }}
      className={`tb-sw relative h-[26px] w-[44px] shrink-0 rounded-full transition-colors ${checked ? 'bg-primary' : 'bg-[#cbc9c4]'}`}
    >
      <span
        className={`tb-sw-thumb absolute top-[3px] block h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.3)] transition-transform ${checked ? 'translate-x-[21px]' : 'translate-x-[3px]'}`}
      />
    </button>
  );
}

export function Card({
  title,
  subtitle,
  className = 'px-5 py-[18px]',
  mb = 'mb-[14px]',
  children,
}: {
  title?: string;
  subtitle?: string;
  className?: string;
  mb?: string;
  children: ReactNode;
}) {
  return (
    <div className={`${mb} rounded-2xl border border-line bg-white ${className}`}>
      {title ? <div className="mb-[3px] text-[13px] font-semibold">{title}</div> : null}
      {subtitle ? <div className="mb-4 text-[12px] text-ink-subtle">{subtitle}</div> : null}
      {children}
    </div>
  );
}
