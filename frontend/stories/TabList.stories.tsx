import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { TabList } from '../src/shared/ui';

const options = [
  { value: 'text', label: 'Текст' },
  { value: 'photo', label: 'Фото' },
  { value: 'stories', label: 'Истории' },
  { value: 'music', label: 'Музыка' },
  { value: 'channels', label: 'Каналы' },
  { value: 'privacy', label: 'Приватность' },
] as const;

function Example() {
  const [value, setValue] = useState<(typeof options)[number]['value']>('text');
  return (
    <div className="w-full rounded-lg border border-line bg-surface-card">
      <TabList
        options={options}
        value={value}
        onChange={setValue}
        idPrefix="story-tab"
        panelId="story-panel"
        ariaLabel="Разделы профиля"
      />
      <div role="tabpanel" id="story-panel" aria-labelledby={`story-tab-${value}`} className="p-6">
        {options.find((option) => option.value === value)?.label}
      </div>
    </div>
  );
}

const meta = {
  title: 'Shared/TabList',
  component: TabList,
  tags: ['autodocs'],
  args: {
    options,
    value: 'text',
    onChange: () => undefined,
    idPrefix: 'story-tab',
    panelId: 'story-panel',
    ariaLabel: 'Разделы профиля',
  },
} satisfies Meta<typeof TabList>;

export default meta;
type Story = StoryObj<typeof meta>;

export const ProfileSections: Story = { render: () => <Example /> };
