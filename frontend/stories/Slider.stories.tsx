import { useState } from 'react';

import type { Meta, StoryObj } from '@storybook/react-vite';

import { Slider } from '../src/shared/ui';

function SliderExample() {
  const [value, setValue] = useState(120);
  return (
    <Slider
      value={value}
      min={10}
      max={600}
      step={10}
      onChange={setValue}
      label="Пауза между сообщениями"
    />
  );
}

const meta = {
  title: 'Shared/Slider',
  component: Slider,
  tags: ['autodocs'],
  args: { value: 120, min: 10, max: 600, onChange: () => undefined, label: 'Пауза' },
} satisfies Meta<typeof Slider>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Enabled: Story = { render: () => <SliderExample /> };
export const Disabled: Story = {
  render: () => (
    <Slider value={120} min={10} max={600} onChange={() => {}} label="Пауза" disabled />
  ),
};
