import { useTranslation } from 'react-i18next';

import { SectionStack, CollapsibleCard, NumberedStep } from '@/shared/ui';

const HOW_STEPS = [0, 1, 2, 3] as const;

// The collapsible "how it works" explainer at the bottom of the page.
export function HowItWorksCard() {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      label={t('neuroshilling.howto.title')}
      appearance="canvas"

      header={<span className="type-card-title">{t('neuroshilling.howto.title')}</span>}
    >
      <SectionStack gap="compact">
        {HOW_STEPS.map((index) => (
          <NumberedStep key={index} number={index + 1}>
            {t(`neuroshilling.howto.steps.${String(index)}`)}
          </NumberedStep>
        ))}
      </SectionStack>
    </CollapsibleCard>
  );
}
