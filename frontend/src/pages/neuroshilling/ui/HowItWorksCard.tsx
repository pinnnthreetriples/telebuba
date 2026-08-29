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
      header={<span className="type-card-title">{t('neuroshilling.howto.title')}</span>}
    >
      <div className="flex flex-col gap-md">
        {HOW_STEPS.map((index) => (
          <div key={index} className="flex items-start gap-md">
            <span className="mt-px flex size-glyph shrink-0 items-center justify-center rounded-full bg-action-primary text-tiny font-semibold text-on-action">
              {index + 1}
            </span>
            <span className="type-prose">{t(`neuroshilling.howto.steps.${String(index)}`)}</span>
          </div>
        ))}
      </div>
    </CollapsibleCard>
  );
}
