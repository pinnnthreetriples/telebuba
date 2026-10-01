import type { Meta, StoryObj } from '@storybook/react-vite';

import { Button, Modal } from '../src/shared/ui';

const meta = {
  title: 'Design System/Components/Modal',
  component: Modal,
  tags: ['autodocs'],
  args: {
    label: 'Настройки прогрева',
    size: 'form',
    onClose: () => undefined,
    children: (
      <div className="flex flex-col gap-lg p-xl">
        <h3 className="type-dialog-title">Настройки прогрева</h3>
        <p className="type-dialog-body">Диалог поверх страницы.</p>
        <div className="flex justify-end">
          <Button variant="primary">Сохранить</Button>
        </div>
      </div>
    ),
  },
} satisfies Meta<typeof Modal>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Open: Story = {};
