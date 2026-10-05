import { useTranslation } from 'react-i18next';

import { CollapsibleCard, NumberedStep } from '@/shared/ui';

const HOW_STEPS = [0, 1, 2, 3] as const;

// The collapsible "how it works" explainer at the bottom of the page.
export function HowItWorksCard() {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      label={t('neuroshilling.howto.title')}
      wrapperClassName="rounded-card border border-line bg-canvas"
      headerClassName="px-lg py-lg"
      header={<span className="type-h3">{t('neuroshilling.howto.title')}</span>}
    >
      <div className="flex flex-col gap-md">
        {HOW_STEPS.map((index) => (
          <NumberedStep key={index} number={index + 1}>
            {t(`neuroshilling.howto.steps.${String(index)}`)}
          </NumberedStep>
        ))}
      </div>
    </CollapsibleCard>
  );
}
