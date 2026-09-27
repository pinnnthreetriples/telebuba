import type { Meta, StoryObj } from '@storybook/react-vite';

import { FeedbackMark } from '../src/shared/ui';

const meta = {
  title: 'Shared/FeedbackMark',
  component: FeedbackMark,
  tags: ['autodocs'],
} satisfies Meta<typeof FeedbackMark>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Idle: Story = {};
export const Ok: Story = { args: { result: 'ok' } };
export const Error: Story = { args: { result: 'err' } };
