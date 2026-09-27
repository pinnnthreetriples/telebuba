import type { Meta, StoryObj } from '@storybook/react-vite';

import { Icon, IconButton, SurfHover } from '../src/shared/ui';

const meta = {
  title: 'Shared/SurfHover',
  component: SurfHover,
  tags: ['autodocs'],
  args: {
    actions: (
      <IconButton size="sm" tone="primary" aria-label="Изменить">
        <Icon name="pencil" size={14} />
      </IconButton>
    ),
    surface: (
      <div className="rounded-lg border border-line bg-surface-card px-md py-sm">
        <div className="type-item-title">Кампания «Крипта»</div>
        <div className="type-caption">4 канала · 120 комментариев</div>
      </div>
    ),
  },
} satisfies Meta<typeof SurfHover>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Closed: Story = {};
export const Open: Story = { args: { open: true } };
