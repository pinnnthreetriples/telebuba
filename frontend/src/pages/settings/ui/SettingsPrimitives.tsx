import type { ReactNode } from 'react';

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
