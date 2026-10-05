import type { Meta, StoryObj } from '@storybook/react-vite';

import { Icon, type IconName } from '../src/shared/ui';

const names: IconName[] = [
  'alert-square',
  'alert-triangle',
  'arrow-right',
  'arrow-swap',
  'chart',
  'check',
  'check-circle',
  'chevron-down',
  'chevron-right',
  'close',
  'eye',
  'eye-off',
  'file',
  'gear',
  'globe',
  'paperclip',
  'pause',
  'pencil',
  'play',
  'plus',
  'refresh',
  'shield-check',
  'sparkles',
  'trash',
  'upload-cloud',
  'user-plus',
  'user-round',
  'users',
  'video',
  'x-circle',
];

const meta = {
  title: 'Shared/Icon',
  component: Icon,
  tags: ['autodocs'],
  args: { name: 'check', size: 16 },
} satisfies Meta<typeof Icon>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const All: Story = {
  render: () => (
    <div className="grid grid-cols-3 gap-lg">
      {names.map((name) => (
        <div key={name} className="flex items-center gap-sm text-content-primary">
          <Icon name={name} size={16} />
          <span className="type-small">{name}</span>
        </div>
      ))}
    </div>
  ),
};
