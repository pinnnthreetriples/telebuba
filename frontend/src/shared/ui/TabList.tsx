import type { KeyboardEvent } from 'react';

import { FOCUS_RING } from '@/shared/design-system';

export type TabOption<T extends string> = { value: T; label: string };

export function TabList<T extends string>({
  options,
  value,
  onChange,
  idPrefix,
  panelId,
  ariaLabel,
}: {
  options: readonly TabOption<T>[];
  value: T;
  onChange: (value: T) => void;
  idPrefix: string;
  panelId: string;
  ariaLabel: string;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: T) => {
    const index = options.findIndex((option) => option.value === current);
    const next =
      event.key === 'ArrowRight'
        ? options[(index + 1) % options.length]
        : event.key === 'ArrowLeft'
          ? options[(index - 1 + options.length) % options.length]
          : event.key === 'Home'
            ? options[0]
            : event.key === 'End'
              ? options[options.length - 1]
              : undefined;
    if (!next) return;
    event.preventDefault();
    onChange(next.value);
    document.getElementById(`${idPrefix}-${next.value}`)?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="tb-scroll flex gap-4 overflow-x-auto border-b border-line-row px-6"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          id={`${idPrefix}-${option.value}`}
          aria-controls={panelId}
          aria-selected={value === option.value}
          tabIndex={value === option.value ? 0 : -1}
          onKeyDown={(event) => onKeyDown(event, option.value)}
          onClick={() => onChange(option.value)}
          className={`shrink-0 whitespace-nowrap border-b-2 py-4 text-body font-medium transition-colors ${FOCUS_RING} ${
            value === option.value
              ? 'border-action-primary text-content-primary'
              : 'border-transparent text-content-muted hover:border-info-line'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
