import { useTranslation } from 'react-i18next';

import { CollapsibleCard } from '@/shared/ui';

const HOW_STEPS = [0, 1, 2, 3] as const;

// The collapsible "how it works" explainer at the bottom of the page.
export function HowItWorksCard() {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      label={t('neuroshilling.howto.title')}
      wrapperClassName="rounded-card border border-line bg-canvas"
      headerClassName="px-lg py-lg"
      header={<span className="text-lead font-semibold">{t('neuroshilling.howto.title')}</span>}
    >
      <div className="flex flex-col gap-md">
        {HOW_STEPS.map((index) => (
          <div key={index} className="flex items-start gap-md">
            <span className="mt-px flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-primary text-micro font-semibold text-white">
              {index + 1}
            </span>
            <span className="text-body leading-[1.5] text-ink-muted">
              {t(`neuroshilling.howto.steps.${String(index)}`)}
            </span>
          </div>
        ))}
      </div>
    </CollapsibleCard>
  );
}
