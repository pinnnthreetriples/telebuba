import type { Meta, StoryObj } from '@storybook/react-vite';

import { ConfirmModal } from '../src/shared/ui';

const meta = {
  title: 'Design System/Components/ConfirmModal',
  component: ConfirmModal,
  tags: ['autodocs'],
  args: {
    title: 'Удалить аккаунт?',
    body: 'Аккаунт и его сессия будут удалены безвозвратно.',
    confirmLabel: 'Удалить',
    cancelLabel: 'Отмена',
    onConfirm: () => undefined,
    onClose: () => undefined,
  },
} satisfies Meta<typeof ConfirmModal>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Open: Story = {};
