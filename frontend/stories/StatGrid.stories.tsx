import type { Meta, StoryObj } from '@storybook/react-vite';

import { StatGrid } from '../src/shared/ui';

const meta = {
  title: 'Patterns/Stat tiles',
  component: StatGrid,
  tags: ['autodocs'],
  args: {
    stats: [
      { label: 'Кампаний', value: 2 },
      { label: 'Каналов', value: 4, tone: 'primary' },
      { label: 'Аккаунтов', value: 2 },
      { label: 'Комментариев', value: 14, tone: 'success' },
      { label: 'Удалено', value: 1, tone: 'danger' },
      { label: 'Ошибок', value: 2, tone: 'danger' },
    ],
  },
} satisfies Meta<typeof StatGrid>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Six: Story = {};
export const Three: Story = {
  args: {
    stats: [
      { label: 'в прогреве', value: 3, tone: 'primary' },
      { label: 'готовы', value: 1 },
      { label: 'ошибки', value: 0, tone: 'danger' },
    ],
  },
};
export const WithText: Story = {
  args: {
    stats: [
      { label: 'Реплик', value: 4 },
      { label: 'Диалог в цели', value: '3:45' },
    ],
  },
};
