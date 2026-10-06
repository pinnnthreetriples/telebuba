import type { Meta, StoryObj } from '@storybook/react-vite';

import { Badge, PipelineCard } from '../src/shared/ui';

// The shared card; the two pages' own compositions are `RuntimePipeline` and
// `LaunchPipeline` (their stories carry real page data).
const meta = {
  title: 'Patterns/Pipeline card',
  component: PipelineCard,
  tags: ['autodocs'],
  decorators: [
    (Story: () => React.ReactNode) => (
      <div className="w-panel max-w-full">
        <Story />
      </div>
    ),
  ],
  args: {
    title: 'Конвейер обработки постов',
    status: (
      <Badge size="sm" tone="success">
        активен
      </Badge>
    ),
    running: true,
    startLabel: 'Запустить',
    stopLabel: 'Остановить',
    onToggle: () => undefined,
    steps: [
      { id: 'listen', label: 'Слушатель', state: 'done' },
      { id: 'detect', label: 'Новый пост', state: 'current' },
      { id: 'filter', label: 'Фильтр', state: 'upcoming' },
      { id: 'comment', label: 'Комментарий', state: 'upcoming' },
    ],
    notice: { tone: 'info', text: 'Слушаю новые посты и комментирую' },
    stats: [
      { label: 'Кампаний', value: 2 },
      { label: 'Каналов', value: 4, tone: 'primary' },
      { label: 'Ошибок', value: 1, tone: 'danger' },
    ],
  },
} satisfies Meta<typeof PipelineCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Running: Story = {};
export const Stopped: Story = {
  args: {
    running: false,
    status: <Badge size="sm">остановлен</Badge>,
    notice: { tone: 'success', text: 'Готово к запуску' },
  },
};
