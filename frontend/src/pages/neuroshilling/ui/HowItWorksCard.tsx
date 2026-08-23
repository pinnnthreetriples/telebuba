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
      headerClassName="px-4 py-[15px]"
      header={<span className="text-[13px] font-semibold">{t('neuroshilling.howto.title')}</span>}
    >
      <div className="flex flex-col gap-[10px]">
        {HOW_STEPS.map((index) => (
          <div key={index} className="flex items-start gap-[10px]">
            <span className="mt-px flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-primary text-[10.5px] font-semibold text-white">
              {index + 1}
            </span>
            <span className="text-[12.5px] leading-[1.5] text-ink-muted">
              {t(`neuroshilling.howto.steps.${String(index)}`)}
            </span>
          </div>
        ))}
      </div>
    </CollapsibleCard>
  );
}
