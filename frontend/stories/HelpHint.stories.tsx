import type { Meta, StoryObj } from '@storybook/react-vite';

import { HelpHint } from '../src/shared/ui';

const meta = {
  title: 'Shared/HelpHint',
  component: HelpHint,
  tags: ['autodocs'],
  args: {
    text: 'Сколько действий аккаунт делает за сутки.',
    example: '20 — прогрев, 60 — рабочий режим',
  },
} satisfies Meta<typeof HelpHint>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
