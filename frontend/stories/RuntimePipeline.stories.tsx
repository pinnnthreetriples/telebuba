import type { Meta, StoryObj } from '@storybook/react-vite';

import { PipelineCard } from '../src/pages/neurocomment/ui/PipelineCard';

const meta = {
  title: 'Design System/Patterns/Runtime pipeline',
  component: PipelineCard,
  tags: ['autodocs'],
  args: {
    running: true,
    canStart: true,
    events: [],
    onToggle: () => undefined,
    stats: [
      { label: 'Кампаний', value: 2, color: 'text-content-primary' },
      { label: 'Каналов', value: 4, color: 'text-action-primary' },
      { label: 'Аккаунтов', value: 2, color: 'text-content-primary' },
      { label: 'Комментариев', value: 14, color: 'text-success-deep' },
      { label: 'Удалено', value: 1, color: 'text-danger' },
      { label: 'Ошибок', value: 2, color: 'text-danger' },
    ],
  },
} satisfies Meta<typeof PipelineCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Running: Story = {};
export const Stopped: Story = { args: { running: false } };
