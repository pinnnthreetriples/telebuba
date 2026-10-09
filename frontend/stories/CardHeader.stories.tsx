import type { Meta, StoryObj } from '@storybook/react-vite';

import { Badge, Card, CardHeader, Icon, IconButton, Switch } from '../src/shared/ui';

const meta = {
  title: 'Patterns/Card header',
  component: CardHeader,
  tags: ['autodocs'],
  decorators: [
    (Story: () => React.ReactNode) => (
      <div className="w-panel">
        <Card>
          <Story />
        </Card>
      </div>
    ),
  ],
  args: { title: 'Доска работ', badge: <Badge tone="info">2 аккаунта</Badge> },
} satisfies Meta<typeof CardHeader>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithAction: Story = {
  args: {
    children: (
      <IconButton size="md" tone="primary" aria-label="Настроить">
        <Icon name="gear" size={16} />
      </IconButton>
    ),
  },
};
export const WithIcon: Story = {
  args: {
    badge: undefined,
    icon: <Icon name="shield-check" size={16} />,
    title: 'Решение капчи',
    subtitle: 'Авто-проход бот-чека',
    children: <Switch checked onChange={() => undefined} label="Решение капчи" />,
  },
};
export const LiveDot: Story = {
  args: { dot: 'live', title: 'Переписка аккаунтов', badge: <Badge tone="success">7 пар</Badge> },
};
