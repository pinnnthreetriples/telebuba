import type { Meta, StoryObj } from '@storybook/react-vite';

import { Button } from '../src/shared/ui';

import { ButtonSizingGuide } from './ButtonSizingGuide';

const meta = {
  title: 'Design System/Components/Button',
  component: Button,
  tags: ['autodocs'],
  args: { children: 'Сохранить' },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Primary: Story = { args: { variant: 'primary' } };
export const Neutral: Story = { args: { variant: 'neutral', children: 'Остановить' } };
export const Secondary: Story = { args: { variant: 'secondary', children: 'Отмена' } };
export const Danger: Story = { args: { variant: 'danger', children: 'Удалить' } };
export const Ghost: Story = { args: { variant: 'ghost', children: 'Подробнее' } };
export const Dashed: Story = { args: { variant: 'dashed', children: 'Добавить кампанию' } };
export const DashedMuted: Story = {
  args: { variant: 'dashedMuted', size: 'xs', children: 'Добавить канал' },
};
export const Disabled: Story = { args: { variant: 'primary', disabled: true } };
export const Loading: Story = { args: { variant: 'primary', loading: true } };
export const GhostAction: Story = { args: { variant: 'ghostAction', children: 'Действие' } };
export const RefreshSuccess: Story = { args: { variant: 'refreshSuccess', children: 'Обновить' } };
export const RefreshDanger: Story = { args: { variant: 'refreshDanger', children: 'Повторить' } };
export const SizesInContext: Story = { render: () => <ButtonSizingGuide /> };
export const ContrastCanvas: Story = {
  render: () => (
    <div className="flex flex-wrap gap-md bg-canvas p-lg">
      <Button variant="ghostAction">Ghost action</Button>
      <Button variant="refreshSuccess">Refresh success</Button>
      <Button variant="refreshDanger">Refresh danger</Button>
    </div>
  ),
};
