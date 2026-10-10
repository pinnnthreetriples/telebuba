import { useTranslation } from 'react-i18next';

import type { AccountFilterOptions } from '@/shared/api';
import { Button, Icon } from '@/shared/ui';

import {
  activeFilterKeys,
  type AccountFilters,
  countryName,
  type FilterKey,
  NO_FILTERS,
  STATUS_LABEL_KEY,
} from '../model/filters';

// The filters in force, one removable chip each, in a strip between the folder tabs and
// the table. Nothing renders when no filter is set.
export function FilterChips({
  filters,
  onChange,
  options,
}: {
  filters: AccountFilters;
  onChange: (filters: AccountFilters) => void;
  options: AccountFilterOptions | undefined;
}) {
  const { t, i18n } = useTranslation();
  const keys = activeFilterKeys(filters);
  if (keys.length === 0) return null;

  const label = (key: FilterKey): string => {
    if (key === 'phone') return t('accounts.filters.chipPhone', { code: filters.phone });
    if (key === 'status' && filters.status) {
      return t('accounts.filters.chipStatus', { status: t(STATUS_LABEL_KEY[filters.status]) });
    }
    if (key === 'minTrust') return t('accounts.filters.chipTrust', { value: filters.minTrust });
    const code = filters.proxy ?? '';
    const country =
      code === 'none'
        ? t('accounts.filters.noProxy')
        : countryName(
            code,
            i18n.language,
            options?.proxy_countries.find((option) => option.country_code === code)?.country_name,
          );
    return t('accounts.filters.chipProxy', { country });
  };

  return (
    <div className="flex flex-wrap items-center gap-2 border-x border-line bg-surface px-4 py-3">
      {keys.map((key) => {
        const text = label(key);
        return (
          <Button
            key={key}
            size="sm"
            aria-label={t('accounts.filters.remove', { label: text })}
            onClick={() => {
              onChange({ ...filters, [key]: null });
            }}
          >
            {text}
            <Icon name="close" size={12} />
          </Button>
        );
      })}
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          onChange(NO_FILTERS);
        }}
      >
        {t('accounts.filters.resetAll')}
      </Button>
    </div>
  );
}
