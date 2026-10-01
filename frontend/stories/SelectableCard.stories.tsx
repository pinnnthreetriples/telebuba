import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { Icon, IconButton, SelectableCard } from '../src/shared/ui';

function Example({
  initiallySelected = false,
  initiallyOpen = false,
}: {
  initiallySelected?: boolean;
  initiallyOpen?: boolean;
}) {
  const [selected, setSelected] = useState(initiallySelected);
  const [actionsOpen, setActionsOpen] = useState(initiallyOpen);
  return (
    <div className="lg:w-sidebar">
      <SelectableCard
        surfaceId="example-campaign"
        name="Крипта"
        meta="4 канала · 3 аккаунта"
        status={
          <span className="inline-flex items-center gap-tight type-caption font-medium text-success-deep">
            <span className="size-dot rounded-full bg-current" />
            Активна
          </span>
        }
        selected={selected}
        actionsOpen={actionsOpen}
        actionsLabel="Действия с кампанией"
        onSelect={() => setSelected(true)}
        onToggleActions={() => setActionsOpen((open) => !open)}
        actions={
          <>
            <IconButton
              size="md"
              tone="warning"
              aria-label="Приостановить кампанию"
              title="Приостановить кампанию"
            >
              <Icon name="pause" size={16} />
            </IconButton>
            <IconButton
              size="md"
              tone="primary"
              aria-label="Изменить кампанию"
              title="Изменить кампанию"
            >
              <Icon name="pencil" size={16} />
            </IconButton>
            <IconButton
              size="md"
              tone="danger"
              aria-label="Удалить кампанию"
              title="Удалить кампанию"
            >
              <Icon name="trash" size={16} />
            </IconButton>
          </>
        }
      />
    </div>
  );
}

const meta = {
  title: 'Design System/Patterns/Campaign selection',
  component: SelectableCard,
  tags: ['autodocs'],
  args: {
    surfaceId: 'example-campaign',
    name: 'Крипта',
    meta: '4 канала · 3 аккаунта',
    status: (
      <span className="inline-flex items-center gap-tight type-caption font-medium text-success-deep">
        <span className="size-dot rounded-full bg-current" />
        Активна
      </span>
    ),
    selected: false,
    actionsOpen: false,
    actionsLabel: 'Действия с кампанией',
    actions: (
      <>
        <IconButton size="md" tone="warning" aria-label="Приостановить кампанию">
          <Icon name="pause" size={16} />
        </IconButton>
        <IconButton size="md" tone="primary" aria-label="Изменить кампанию">
          <Icon name="pencil" size={16} />
        </IconButton>
        <IconButton size="md" tone="danger" aria-label="Удалить кампанию">
          <Icon name="trash" size={16} />
        </IconButton>
      </>
    ),
    onSelect: () => undefined,
    onToggleActions: () => undefined,
  },
} satisfies Meta<typeof SelectableCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = { render: () => <Example /> };
export const Selected: Story = { render: () => <Example initiallySelected /> };
export const ActionsOpen: Story = { render: () => <Example initiallySelected initiallyOpen /> };
