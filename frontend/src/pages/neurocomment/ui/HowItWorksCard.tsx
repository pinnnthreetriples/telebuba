import { useTranslation } from 'react-i18next';

import { SectionStack, CollapsibleCard, NumberedStep } from '@/shared/ui';

const HOW_STEPS = [0, 1, 2, 3] as const;

// The collapsible "how it works" explainer at the bottom of the left column.
export function HowItWorksCard() {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      label={t('neurocomment.howto.title')}
      appearance="canvas"

      header={<span className="type-card-title">{t('neurocomment.howto.title')}</span>}
    >
      <SectionStack gap="compact">
        {HOW_STEPS.map((index) => (
          <NumberedStep key={index} number={index + 1}>
            {t(`neurocomment.howto.steps.${String(index)}`)}
          </NumberedStep>
        ))}
      </SectionStack>
    </CollapsibleCard>
  );
}
