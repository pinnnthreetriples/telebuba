import type { Meta, StoryObj } from '@storybook/react-vite';

import { Stepper } from '../src/shared/ui';

const meta = {
  title: 'Patterns/Stepper',
  component: Stepper,
  tags: ['autodocs'],
  decorators: [
    (Story: () => React.ReactNode) => (
      <div className="w-panel">
        <Story />
      </div>
    ),
  ],
  args: {
    pulse: true,
    steps: [
      { id: 'subscribe', label: 'Подписка', state: 'done' },
      { id: 'read', label: 'Чтение', state: 'done' },
      { id: 'reactions', label: 'Реакции', state: 'current' },
      { id: 'stories', label: 'Сторис', state: 'upcoming' },
      { id: 'pause', label: 'Пауза', state: 'upcoming' },
    ],
  },
} satisfies Meta<typeof Stepper>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Cycle: Story = {};
export const CurrentOnNarrow: Story = { args: { narrow: 'current' } };
export const Conditions: Story = {
  args: {
    pulse: false,
    narrow: 'list',
    steps: [
      { id: 'scenario', label: 'Сценарий', caption: 'утверждён', state: 'done' },
      { id: 'accounts', label: 'Аккаунты', caption: '1 из 2 ролей', state: 'upcoming' },
      { id: 'targets', label: 'Цели', caption: '2 цели', state: 'done' },
    ],
  },
};
export const Wizard: Story = {
  args: {
    pulse: false,
    numbered: true,
    steps: [
      { id: '1', state: 'done' },
      { id: '2', state: 'current' },
      { id: '3', state: 'upcoming' },
    ],
  },
};
