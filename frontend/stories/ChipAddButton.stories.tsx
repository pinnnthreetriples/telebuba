import type { Meta, StoryObj } from '@storybook/react-vite';

import { ChipAddButton } from '../src/shared/ui';

const CHANNELS = ['Streetfights66', 'vk_dating', 'DeCenter', 'moscowmap', 'shot_shot'];

const meta = {
  title: 'Patterns/Chip add button',
  component: ChipAddButton,
  tags: ['autodocs'],
  args: { children: '+ Канал' },
} satisfies Meta<typeof ChipAddButton>;

export default meta;
type Story = StoryObj<typeof meta>;

// Эталон: пунктирная пилюля высоты md в конце ряда чипов (прогрев, нейрокомментинг,
// нейрошиллинг). Размер не выбирается на месте вызова.
export const InChipRow: Story = {
  render: (args) => (
    <div className="flex flex-wrap items-center gap-sm rounded-card border border-line bg-surface-card p-lg">
      {CHANNELS.map((channel) => (
        <span
          key={channel}
          className="rounded-full border border-line bg-canvas px-md py-xs type-caption"
        >
          {channel}
        </span>
      ))}
      <ChipAddButton {...args} />
    </div>
  ),
};

export const Disabled: Story = { args: { disabled: true } };
