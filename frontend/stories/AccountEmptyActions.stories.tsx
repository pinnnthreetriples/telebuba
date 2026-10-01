import type { Meta, StoryObj } from '@storybook/react-vite';

import { DashedAdd, DashedEmptyAction } from '../src/widgets/account-edit/ui/_shared';

const meta = {
  title: 'Design System/Patterns/Account empty actions',
  component: DashedEmptyAction,
  tags: ['autodocs'],
  args: {
    idleLabel: 'У аккаунта пока нет каналов',
    actionLabel: 'Создать канал',
    onClick: () => undefined,
  },
} satisfies Meta<typeof DashedEmptyAction>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Channels: Story = {};
export const Music: Story = {
  args: { idleLabel: 'Музыки пока нет', actionLabel: 'Добавить музыку' },
};
export const PhotoTile: Story = {
  render: () => (
    <div className="w-col">
      <DashedAdd ratio="1" label="Загрузить" onClick={() => undefined} />
    </div>
  ),
};
export const StoryTile: Story = {
  render: () => (
    <div className="w-col">
      <DashedAdd ratio="9 / 16" label="Добавить сторис" onClick={() => undefined} />
    </div>
  ),
};
