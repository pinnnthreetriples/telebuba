import { useTranslation } from 'react-i18next';

import { Notice } from '@/shared/ui';

// The page's geo banner: how many accounts have a phone/proxy country mismatch the
// operator has not accepted yet. Nothing is blocked — the start dialog asks.
export function GeoMismatchBanner({ count }: { count: number }) {
  const { t } = useTranslation();
  if (count <= 0) return null;
  return (
    <div className="mb-4">
      <Notice tone="warning" role="status">
        <p className="m-0 type-body-medium">{t('warming.geo.banner', { count })}</p>
        <p className="m-0 type-small">{t('warming.geo.bannerDetail')}</p>
      </Notice>
    </div>
  );
}
