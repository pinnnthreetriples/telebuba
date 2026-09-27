import type { Meta, StoryObj } from '@storybook/react-vite';

import { CloseButton } from '../src/shared/ui';

const meta = {
  title: 'Shared/CloseButton',
  component: CloseButton,
  tags: ['autodocs'],
  args: { 'aria-label': 'Закрыть' },
} satisfies Meta<typeof CloseButton>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Small: Story = { args: { size: 'sm' } };
export const Touch: Story = { args: { size: 'touch' } };
export const Disabled: Story = { args: { disabled: true } };
