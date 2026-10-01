import { useState } from 'react';

import type { Meta, StoryObj } from '@storybook/react-vite';

import { Select } from '../src/shared/ui';

const options = [
  { value: 'socks5', label: 'SOCKS5' },
  { value: 'http', label: 'HTTP' },
  { value: 'mtproto', label: 'MTProto', disabled: true },
];

function SelectExample() {
  const [value, setValue] = useState('socks5');
  return <Select value={value} onChange={setValue} options={options} ariaLabel="Протокол" />;
}

const meta = {
  title: 'Design System/Components/Select',
  component: Select,
  tags: ['autodocs'],
  args: { value: 'socks5', onChange: () => undefined, options, ariaLabel: 'Протокол' },
} satisfies Meta<typeof Select>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Value: Story = { render: () => <SelectExample /> };
export const Disabled: Story = {
  render: () => (
    <Select value="socks5" onChange={() => {}} options={options} ariaLabel="Протокол" disabled />
  ),
};
