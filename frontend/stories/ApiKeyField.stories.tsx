import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiKeyField } from '../src/pages/settings/ui/ApiKeyField';
import { Card } from '../src/shared/ui';

function KeyExample({ stored, disabled }: { stored: boolean; disabled: boolean }) {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const [show, setShow] = useState(false);
  const [cleared, setCleared] = useState(false);
  return (
    <Card>
      <fieldset disabled={disabled} className="m-0 min-w-0 border-0 p-0">
        <ApiKeyField
          label={t('settings.api.geminiKey')}
          value={value}
          show={show}
          keySet={stored && !cleared}
          placeholder={
            cleared
              ? t('settings.api.keyCleared')
              : stored
                ? t('settings.api.keySet')
                : t('settings.api.keyUnset')
          }
          toggleLabel={t('settings.api.toggleVisibility')}
          clearLabel={t('settings.api.clearKey')}
          onChange={setValue}
          onToggleShow={() => setShow(!show)}
          onClear={() => {
            setValue('');
            setCleared(true);
          }}
        />
      </fieldset>
    </Card>
  );
}
const meta = {
  title: 'Design System/Patterns/ApiKeyField',
  component: KeyExample,
  parameters: {
    docs: {
      description: {
        component:
          'Поле настоящей страницы настроек. В истории нет сохранённого ключа: stored показывает только состояние. Ввод, переключение видимости и очистка локальны.',
      },
    },
  },
  args: { stored: true, disabled: false },
  render: (args) => <KeyExample key={`${args.stored}:${args.disabled}`} {...args} />,
} satisfies Meta<typeof KeyExample>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Stored: Story = {};
export const Unset: Story = { args: { stored: false } };
export const Disabled: Story = { args: { disabled: true } };
