import { useState } from 'react';

import type { Meta, StoryObj } from '@storybook/react-vite';

import type { LogEntry } from '../src/shared/api';
import { ConfirmModal } from '../src/shared/ui';
import { LogTerminal } from '../src/widgets/log-terminal';

const lines: LogEntry[] = [
  {
    id: 1,
    created_at: '2026-08-28T12:00:00Z',
    level: 'INFO',
    status: 'success',
    account_id: 'acc-1',
    event: 'neuroshilling_message_sent',
    extra: { channel: '@crypto_daily' },
  },
  {
    id: 2,
    created_at: '2026-08-28T12:01:00Z',
    level: 'WARNING',
    status: 'warning',
    account_id: 'acc-2',
    event: 'neuroshilling_run_stopped',
    extra: {},
  },
];

const meta = {
  title: 'Design System/Patterns/Activity log',
  component: LogTerminal,
  tags: ['autodocs'],
  args: {
    title: 'Лог кампании',
    logLines: lines,
    accountName: (id: string) => (id === 'acc-1' ? 'Иван Петров' : 'Мария Смирнова'),
  },
  decorators: [
    (Story: () => React.ReactNode) => (
      <div className="w-panel">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof LogTerminal>;

export default meta;
type Story = StoryObj<typeof meta>;

function InteractiveLogTerminal(args: Parameters<typeof LogTerminal>[0]) {
  const [entries, setEntries] = useState(args.logLines);
  const [confirmClear, setConfirmClear] = useState(false);

  return (
    <>
      <LogTerminal {...args} logLines={entries} onClear={() => setConfirmClear(true)} />
      {confirmClear && (
        <ConfirmModal
          title="Очистить журнал?"
          body="Записи журнала будут удалены."
          confirmLabel="Очистить"
          cancelLabel="Отмена"
          onClose={() => setConfirmClear(false)}
          onConfirm={() => setEntries([])}
        />
      )}
    </>
  );
}

export const Populated: Story = {
  render: (args) => <InteractiveLogTerminal {...args} />,
};
export const Empty: Story = { args: { logLines: [] } };
