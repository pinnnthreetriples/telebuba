import { type ReactNode, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountFilterOptions } from '@/shared/api';
import { surface } from '@/shared/design-system';
import { cn } from '@/shared/lib';
import { Button, Icon, IconButton } from '@/shared/ui';

import {
  activeFilterKeys,
  type AccountFilters,
  countryName,
  NO_FILTERS,
  STATUS_BUCKETS,
  STATUS_DOT,
  STATUS_LABEL_KEY,
  TRUST_STEPS,
} from '../model/filters';
import { Flag, FilterPill } from './FilterPill';

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="radiogroup" aria-label={label}>
      <div className="mb-2 type-small">{label}</div>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

// The sliders button and the card it opens. A card of pill toggles, not a form: every
// pill applies at once, and «Показать» only closes the card.
export function FilterMenu({
  filters,
  onChange,
  options,
  found,
}: {
  filters: AccountFilters;
  onChange: (filters: AccountFilters) => void;
  options: AccountFilterOptions | undefined;
  found: number | undefined;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const active = activeFilterKeys(filters).length > 0;

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (card.current?.contains(target) || button.current?.contains(target)) return;
      setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  const set = (patch: Partial<AccountFilters>) => {
    onChange({ ...filters, ...patch });
  };
  const close = () => {
    setOpen(false);
    button.current?.focus();
  };

  return (
    // Not `relative`: the card hangs from the tab strip's right edge, which is positioned,
    // so on a phone it stays on screen instead of following the button off the left edge.
    <div className="flex">
      <IconButton
        ref={button}
        size="md"
        tone={active || open ? 'primary' : 'neutral'}
        aria-label={t('accounts.filters.open')}
        title={t('accounts.filters.open')}
        aria-expanded={open}
        aria-controls="account-filters"
        className={cn(active && 'border-info-line bg-info-tint text-info-strong')}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <Icon name="sliders-horizontal" size={16} />
      </IconButton>
      {open ? (
        <div
          ref={card}
          id="account-filters"
          role="region"
          aria-label={t('accounts.filters.title')}
          className={surface(
            'panel',
            'absolute right-0 top-full z-dialog mt-2 w-panel max-w-[90vw] px-4 py-4',
          )}
        >
          <div className="mb-4 flex items-center justify-between gap-3">
            <span className="type-h3">{t('accounts.filters.title')}</span>
            {active ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  onChange(NO_FILTERS);
                }}
              >
                {t('accounts.filters.reset')}
              </Button>
            ) : null}
          </div>
          <div className="flex flex-col gap-4">
            <Group label={t('accounts.filters.phone')}>
              <FilterPill selected={filters.phone === null} onSelect={() => set({ phone: null })}>
                {t('accounts.filters.all')}
              </FilterPill>
              {options?.phone_codes.map((option) => (
                <FilterPill
                  key={option.calling_code}
                  selected={filters.phone === option.calling_code}
                  onSelect={() => set({ phone: option.calling_code })}
                  title={
                    option.country_code
                      ? `+${String(option.calling_code)} · ${countryName(option.country_code, i18n.language)}`
                      : `+${String(option.calling_code)}`
                  }
                >
                  {option.country_code ? <Flag code={option.country_code} /> : null}+
                  {option.calling_code}
                </FilterPill>
              ))}
            </Group>
            <Group label={t('accounts.filters.proxy')}>
              <FilterPill selected={filters.proxy === null} onSelect={() => set({ proxy: null })}>
                {t('accounts.filters.any')}
              </FilterPill>
              {options?.proxy_countries.map((option) => (
                <FilterPill
                  key={option.country_code}
                  selected={filters.proxy === option.country_code}
                  onSelect={() => set({ proxy: option.country_code })}
                  title={countryName(option.country_code, i18n.language, option.country_name)}
                >
                  <Flag code={option.country_code} />
                </FilterPill>
              ))}
              {options && options.no_proxy_count > 0 ? (
                <FilterPill
                  selected={filters.proxy === 'none'}
                  onSelect={() => set({ proxy: 'none' })}
                >
                  {t('accounts.filters.noProxy')}
                </FilterPill>
              ) : null}
            </Group>
            <Group label={t('accounts.filters.status')}>
              <FilterPill selected={filters.status === null} onSelect={() => set({ status: null })}>
                {t('accounts.filters.all')}
              </FilterPill>
              {STATUS_BUCKETS.map((bucket) => (
                <FilterPill
                  key={bucket}
                  selected={filters.status === bucket}
                  onSelect={() => set({ status: bucket })}
                >
                  <span
                    aria-hidden
                    className={`size-dot shrink-0 rounded-full ${STATUS_DOT[bucket]}`}
                  />
                  {t(STATUS_LABEL_KEY[bucket])}
                </FilterPill>
              ))}
            </Group>
            <Group label={t('accounts.filters.trust')}>
              <FilterPill
                selected={filters.minTrust === null}
                onSelect={() => set({ minTrust: null })}
              >
                {t('accounts.filters.any')}
              </FilterPill>
              {TRUST_STEPS.map((step) => (
                <FilterPill
                  key={step}
                  selected={filters.minTrust === step}
                  onSelect={() => set({ minTrust: step })}
                >
                  {t('accounts.filters.trustAtLeast', { value: step })}
                </FilterPill>
              ))}
            </Group>
          </div>
          <div className="mt-4 flex items-center justify-between gap-3 border-t border-line pt-4">
            <span className="type-small" aria-live="polite">
              {found === undefined ? null : t('accounts.filters.found', { count: found })}
            </span>
            <Button size="sm" variant="primary" onClick={close}>
              {t('accounts.filters.show')}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
