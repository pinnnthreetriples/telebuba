import type { Meta, StoryObj } from '@storybook/react-vite';

import { CollapsibleCard } from '../src/shared/ui';

const meta = {
  title: 'Design System/Components/CollapsibleCard',
  component: CollapsibleCard,
  tags: ['autodocs'],
  args: {
    label: 'Ограничения',
    header: <span className="type-card-title">Ограничения</span>,
    children: <p className="type-prose">Тело раскрывается по клику на шапку.</p>,
  },
} satisfies Meta<typeof CollapsibleCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Closed: Story = {};
export const Open: Story = { args: { defaultOpen: true } };
