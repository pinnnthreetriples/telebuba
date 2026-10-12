import { useId } from 'react';
import { useTranslation } from 'react-i18next';

import { Notice } from '@/shared/ui';

import { regionName } from '../lib/geo';

// The start dialog's geo warning for one account. Nothing is refused: the operator
// either ticks «Запустить всё равно» — remembered for this pair of countries — or goes
// to change the proxy on the accounts page, where proxies are assigned.
export function GeoConsentNotice({
  phoneCountry,
  proxyCountry,
  accepted,
  onAcceptedChange,
}: {
  phoneCountry: string;
  proxyCountry: string;
  accepted: boolean;
  onAcceptedChange: (accepted: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const checkboxId = useId();
  return (
    <div className="mb-6">
      <Notice tone="warning">
        <p className="m-0 type-body">
          {t('warming.geo.single', {
            proxy: regionName(proxyCountry, i18n.language),
            phone: regionName(phoneCountry, i18n.language),
          })}
        </p>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <label htmlFor={checkboxId} className="inline-flex items-center gap-2 type-body-medium">
            <input
              id={checkboxId}
              type="checkbox"
              checked={accepted}
              onChange={(event) => {
                onAcceptedChange(event.target.checked);
              }}
            />
            {t('warming.geo.accept')}
          </label>
          {/* Proxies are assigned in the account editor on the accounts page. */}
          <a href="/" className="type-body-medium text-action-primary underline">
            {t('warming.geo.changeProxy')}
          </a>
        </div>
      </Notice>
    </div>
  );
}

// The pool's one consent: how many of the accounts it would start have a mismatch.
export function PoolGeoNotice({ count, total }: { count: number; total: number }) {
  const { t } = useTranslation();
  return (
    <div className="mb-6">
      <Notice tone="warning">
        <p className="m-0 type-body-medium">{t('warming.geo.pool', { count, total })}</p>
        <p className="m-0 mt-1 type-small">{t('warming.geo.poolDetail')}</p>
      </Notice>
    </div>
  );
}
