import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { Select } from '@/shared/ui';

import { proxyPoolQueryOptions } from '../api/proxy.queries';
import { proxyTypeLabel } from '../model/proxy';

// One pool proxy to put something on: the proxies with a free slot, plus the one it
// is on now (a full proxy still shows what is selected). The country leads the
// label, since that is what the operator picks a proxy for.
export function ProxyPoolSelect({
  value,
  onChange,
  disabled,
  emptyLabel,
}: {
  value: string | null;
  onChange: (proxyId: string) => void;
  disabled?: boolean;
  emptyLabel?: string;
}) {
  const { t } = useTranslation();
  const pool = useQuery(proxyPoolQueryOptions());
  const choices = (pool.data?.proxies ?? []).filter(
    (proxy) => proxy.free > 0 || proxy.id === value,
  );
  return (
    <Select
      value={value ?? ''}
      disabled={disabled}
      onChange={onChange}
      options={choices.map((proxy) => ({
        value: proxy.id,
        label: [
          proxy.country_code,
          `${proxy.host}:${String(proxy.port)}`,
          proxyTypeLabel(proxy.proxy_type),
        ]
          .filter(Boolean)
          .join(' · '),
      }))}
      placeholder={t('accounts.edit.choosePoolProxy')}
      ariaLabel={t('accounts.proxyPool.title')}
      emptyLabel={emptyLabel}
    />
  );
}
