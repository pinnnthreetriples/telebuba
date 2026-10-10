import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Icon, IconButton } from '@/shared/ui';

// The collapsible search field of the accounts toolbar: a magnifier that opens a pill
// field. Collapsed it is zero-width, invisible and out of the tab order; Escape clears
// it and hands focus back to the magnifier.
export function SearchToggle({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const expanded = open || value !== '';
  const input = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  return (
    <div className="flex min-w-0 items-center gap-2">
      <div
        // The width/opacity animation comes from `.tb-time` (index.css).
        aria-hidden={!expanded}
        className={`tb-time h-compact overflow-hidden rounded-full border bg-surface-card ${
          expanded
            ? 'w-tip max-w-[40vw] border-line opacity-100'
            : 'invisible w-0 border-transparent opacity-0'
        }`}
      >
        <input
          ref={input}
          tabIndex={expanded ? 0 : -1}
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
          }}
          onBlur={() => {
            if (value === '') setOpen(false);
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Escape') return;
            onChange('');
            setOpen(false);
            // Focusing the magnifier also blurs the field, so no separate blur() call.
            button.current?.focus();
          }}
          placeholder={t('accounts.searchPlaceholder')}
          className="h-full w-full border-none bg-surface-card px-3 py-0 text-body outline-hidden"
        />
      </div>
      <IconButton
        ref={button}
        size="md"
        tone="neutral"
        aria-label={t('accounts.searchPlaceholder')}
        title={t('accounts.searchPlaceholder')}
        onClick={() => {
          setOpen(true);
          window.setTimeout(() => input.current?.focus(), 0);
        }}
      >
        <Icon name="search" size={16} />
      </IconButton>
    </div>
  );
}
