import type { Meta, StoryObj } from '@storybook/react-vite';

import { Button, Toaster, toastError } from '../src/shared/ui';

const meta = {
  title: 'Shared/Toaster',
  component: Toaster,
  tags: ['autodocs'],
} satisfies Meta<typeof Toaster>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Error: Story = {
  render: () => (
    <>
      <Button onClick={() => toastError('Не удалось сохранить изменения')}>
        Показать уведомление
      </Button>
      <Toaster />
    </>
  ),
};
