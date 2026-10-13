import { useTranslation } from 'react-i18next';

import type { SurvivalCause } from '@/shared/api';

// «спам-лимит 6 · сессия умерла 1» — deaths by cause, the commonest first.
export function CausesList({ causes }: { causes: readonly SurvivalCause[] }) {
  const { t } = useTranslation();
  return (
    <div>
      <div className="type-small-medium">{t('warming.survival.causes.title')}</div>
      <div className="mt-1 type-body">
        {causes.length === 0
          ? t('warming.survival.causes.none')
          : causes
              .map((item) =>
                t('warming.survival.causes.item', {
                  cause: t(`warming.survival.causes.${item.cause}`),
                  count: item.count,
                }),
              )
              .join(' · ')}
      </div>
    </div>
  );
}
