import type { Meta, StoryObj } from '@storybook/react-vite';

import { Card } from '../src/shared/ui';

const meta = {
  title: 'Shared/Card',
  component: Card,
  tags: ['autodocs'],
  args: { children: <p className="type-prose">Карточка с содержимым.</p> },
} satisfies Meta<typeof Card>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Body: Story = {};
export const Heading: Story = { args: { title: 'Прокси', subtitle: '12 из 40 занято' } };
