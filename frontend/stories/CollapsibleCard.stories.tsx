import type { Meta, StoryObj } from '@storybook/react-vite';

import { CollapsibleCard } from '../src/shared/ui';

const meta = {
  title: 'Shared/CollapsibleCard',
  component: CollapsibleCard,
  tags: ['autodocs'],
  args: {
    label: 'Ограничения',
    title: 'Ограничения',
    children: <p className="type-body text-content-subtle">Тело раскрывается по клику на шапку.</p>,
  },
} satisfies Meta<typeof CollapsibleCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Closed: Story = {};
export const Open: Story = { args: { defaultOpen: true } };
