import type { Meta, StoryObj } from '@storybook/react-vite';

import { NumberedStep } from '../src/shared/ui';

const meta = {
  title: 'Patterns/Numbered steps',
  component: NumberedStep,
  tags: ['autodocs'],
  args: { number: 1, children: 'Подключите аккаунт и проверьте его состояние.' },
} satisfies Meta<typeof NumberedStep>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Single: Story = {};
export const Checklist: Story = {
  render: () => (
    <div className="grid gap-md md:grid-cols-2">
      <NumberedStep number={1}>Выберите аккаунты.</NumberedStep>
      <NumberedStep number={2}>Настройте каналы.</NumberedStep>
      <NumberedStep number={3}>Проверьте ограничения.</NumberedStep>
      <NumberedStep number={4}>Запустите кампанию.</NumberedStep>
    </div>
  ),
};
