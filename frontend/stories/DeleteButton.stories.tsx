import { useState, type ReactNode } from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { DeleteButton } from '../src/shared/ui';

// Ported from Devigner UI (https://ui.devigner.cc/components/delete-button). Press the
// tile: the lid swings open and the confirm pill slides out; ✓ deletes, ✕ or Escape keeps.
const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function Stage({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-6 rounded-lg bg-canvas p-8">{children}</div>
  );
}

function Log() {
  const [events, setEvents] = useState<string[]>([]);
  const push = (event: string) => setEvents((list) => [event, ...list].slice(0, 4));
  return (
    <div className="flex items-center gap-6">
      <DeleteButton
        onConfirm={() => wait(900).then(() => push('удалено'))}
        onCancel={() => push('оставлено')}
      />
      <ul className="type-small">
        {events.map((event, i) => (
          <li key={`${String(i)}-${event}`}>{event}</li>
        ))}
      </ul>
    </div>
  );
}

const meta = {
  title: 'Shared/DeleteButton',
  component: DeleteButton,
  tags: ['autodocs'],
  decorators: [
    (Story) => (
      <Stage>
        <Story />
      </Stage>
    ),
  ],
} satisfies Meta<typeof DeleteButton>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Resting: a trash tile. */
export const Idle: Story = {};

/** The confirm pill open, as after one press. */
export const Armed: Story = { args: { defaultStatus: 'armed' } };

/** `onConfirm` returns a promise: a spinner after a 120ms grace, then the drawn tick. */
export const Async: Story = { args: { onConfirm: () => wait(1200) } };

/** A rejected promise goes back to the trash; the error goes to `onError`. */
export const Failing: Story = {
  args: {
    onConfirm: () => wait(800).then(() => Promise.reject(new Error('Нет связи'))),
    // A real caller toasts here; the story only has to keep the rejection handled.
    onError: () => undefined,
  },
};

/** The pill opens on the left, and the lid swings toward it. */
export const OpensLeft: Story = { args: { side: 'left', defaultStatus: 'armed' } };

export const Disabled: Story = { args: { disabled: true } };

/** Confirm and cancel, logged. */
export const WithLog: Story = { render: () => <Log /> };
