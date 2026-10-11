// Section 1: the mode, two outline cards — a title and one line each.
import { useTranslation } from 'react-i18next';

import { SegmentedControl } from '@/shared/ui';

import type { Settings } from '../../model/draft';

import { Section } from './fields';

const MODES = ['stories', 'priming'] as const;

export function ModeSection({
  mode,
  onChange,
}: {
  mode: Settings['mode'];
  onChange: (mode: Settings['mode']) => void;
}) {
  const { t } = useTranslation();
  return (
    <Section
      n={1}
      title={t('masstagging.settings.mode.title')}
      hint={t('masstagging.settings.mode.hint')}
      example={t('masstagging.settings.mode.example')}
    >
      <SegmentedControl
        variant="outline"
        className="grid gap-3 sm:grid-cols-2"
        value={mode}
        ariaLabel={t('masstagging.settings.mode.title')}
        options={MODES.map((value) => ({
          value,
          label: (
            <span className="block py-1 text-left">
              <span className="block type-body-medium">
                {t(`masstagging.settings.mode.${value}`)}
              </span>
              <span className="block type-small">
                {t(`masstagging.settings.mode.${value}Line`)}
              </span>
            </span>
          ),
        }))}
        onChange={onChange}
      />
    </Section>
  );
}
