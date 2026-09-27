import { useState } from 'react';

import type { Meta, StoryObj } from '@storybook/react-vite';

import { Switch } from '../src/shared/ui';

function SwitchExample() {
  const [checked, setChecked] = useState(true);
  return <Switch checked={checked} onChange={setChecked} label="Автоответ" />;
}

const meta = {
  title: 'Shared/Switch',
  component: Switch,
  tags: ['autodocs'],
  args: { checked: true, onChange: () => undefined, label: 'Автоответ' },
} satisfies Meta<typeof Switch>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Enabled: Story = { render: () => <SwitchExample /> };
export const Disabled: Story = {
  render: () => <Switch checked onChange={() => {}} label="Автоответ" disabled />,
};
