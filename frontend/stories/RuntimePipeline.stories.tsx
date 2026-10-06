import type { Meta, StoryObj } from '@storybook/react-vite';

import { RuntimePipeline } from '../src/pages/neurocomment/ui/RuntimePipeline';

const meta = {
  title: 'Patterns/Runtime pipeline',
  component: RuntimePipeline,
  tags: ['autodocs'],
  args: {
    running: true,
    canStart: true,
    events: [],
    onToggle: () => undefined,
    stats: [
      { label: 'Кампаний', value: 2, tone: 'default' },
      { label: 'Каналов', value: 4, tone: 'primary' },
      { label: 'Аккаунтов', value: 2, tone: 'default' },
      { label: 'Комментариев', value: 14, tone: 'success' },
      { label: 'Удалено', value: 1, tone: 'danger' },
      { label: 'Ошибок', value: 2, tone: 'danger' },
    ],
  },
} satisfies Meta<typeof RuntimePipeline>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Running: Story = {};
export const Stopped: Story = { args: { running: false } };
