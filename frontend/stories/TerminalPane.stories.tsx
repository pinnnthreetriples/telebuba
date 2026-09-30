import type { Meta, StoryObj } from '@storybook/react-vite';

import { TerminalPane } from '../src/shared/ui';

const ROWS = [
  { time: '12:50:19', channel: 'moscowmap', event: 'Новый пост' },
  { time: '12:27:07', channel: 'shot_shot', event: 'Комментарий опубликован' },
  { time: '12:26:58', channel: 'shot_shot', event: 'Генерируем текст' },
];

const meta = {
  title: 'Patterns/Terminal pane',
  component: TerminalPane,
  tags: ['autodocs'],
  decorators: [
    (Story: () => React.ReactNode) => (
      <div className="w-panel">
        <Story />
      </div>
    ),
  ],
  args: {
    children: ROWS.map(({ time, channel, event }) => (
      <div key={time + event} className="flex gap-sm">
        <span className="text-term-dim">{time}</span>
        <span className="text-term-link">{channel}</span>
        <span className="text-term-success">{event}</span>
      </div>
    )),
  },
} satisfies Meta<typeof TerminalPane>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Feed: Story = {};
export const Inline: Story = { args: { size: 'inline' } };
