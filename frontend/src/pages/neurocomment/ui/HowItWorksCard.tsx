import { useTranslation } from 'react-i18next';

import { CollapsibleCard } from '@/shared/ui';

const HOW_STEPS = [0, 1, 2, 3] as const;

// The collapsible "how it works" explainer at the bottom of the left column.
export function HowItWorksCard() {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      label={t('neurocomment.howto.title')}
      wrapperClassName="rounded-card border border-line bg-canvas"
      headerClassName="px-4 py-[15px]"
      header={<span className="text-[13px] font-semibold">{t('neurocomment.howto.title')}</span>}
    >
      <div className="flex flex-col gap-md">
        {HOW_STEPS.map((index) => (
          <div key={index} className="flex items-start gap-md">
            <span className="mt-px flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-primary text-[10.5px] font-semibold text-white">
              {index + 1}
            </span>
            <span className="text-[12.5px] leading-[1.5] text-ink-muted">
              {t(`neurocomment.howto.steps.${String(index)}`)}
            </span>
          </div>
        ))}
      </div>
    </CollapsibleCard>
  );
}
