import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { InlineChipEditor } from '../src/shared/ui';

function Example() {
  const [value, setValue] = useState('');
  const [channels, setChannels] = useState<string[]>(['@news']);
  return (
    <div className="flex flex-wrap items-center gap-sm rounded-card border border-line bg-surface-card p-lg">
      {channels.map((channel) => (
        <span
          key={channel}
          className="rounded-full border border-line bg-canvas px-md py-xs type-caption"
        >
          {channel}
        </span>
      ))}
      <InlineChipEditor
        value={value}
        onChange={setValue}
        onConfirm={() => {
          if (!value.trim()) return;
          setChannels((current) => [...current, value.trim()]);
          setValue('');
        }}
        onCancel={() => setValue('')}
        placeholder="@channel"
        inputLabel="Новый канал"
        confirmLabel="Добавить канал"
        cancelLabel="Отмена"
      />
    </div>
  );
}

const meta = {
  title: 'Patterns/Inline chip editor',
  component: InlineChipEditor,
  tags: ['autodocs'],
  args: {
    value: '',
    onChange: () => undefined,
    onConfirm: () => undefined,
    onCancel: () => undefined,
    placeholder: '@channel',
    inputLabel: 'Новый канал',
    confirmLabel: 'Добавить канал',
  },
} satisfies Meta<typeof InlineChipEditor>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithChannels: Story = { render: () => <Example /> };
