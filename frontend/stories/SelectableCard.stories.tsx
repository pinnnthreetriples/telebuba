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
              size="touch"
              tone="neutral"
              aria-label="Приостановить кампанию"
              title="Приостановить кампанию"
              className="w-action self-stretch text-warning-deep hover:bg-warning-tint"
            >
              <Icon name="pause" size={18} />
            </IconButton>
            <IconButton
              size="touch"
              tone="primary"
              aria-label="Изменить кампанию"
              title="Изменить кампанию"
              className="w-action self-stretch"
            >
              <Icon name="pencil" size={18} />
            </IconButton>
            <IconButton
              size="touch"
              tone="danger"
              aria-label="Удалить кампанию"
              title="Удалить кампанию"
              className="w-action self-stretch"
            >
              <Icon name="trash" size={18} />
            </IconButton>
          </>
        }
      />
    </div>
  );
}

const meta = {
  title: 'Patterns/Campaign selection',
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
        <IconButton
          size="touch"
          tone="neutral"
          aria-label="Приостановить кампанию"
          className="w-action self-stretch text-warning-deep hover:bg-warning-tint"
        >
          <Icon name="pause" size={18} />
        </IconButton>
        <IconButton
          size="touch"
          tone="primary"
          aria-label="Изменить кампанию"
          className="w-action self-stretch"
        >
          <Icon name="pencil" size={18} />
        </IconButton>
        <IconButton
          size="touch"
          tone="danger"
          aria-label="Удалить кампанию"
          className="w-action self-stretch"
        >
          <Icon name="trash" size={18} />
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
