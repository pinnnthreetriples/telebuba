import type { Meta, StoryObj } from '@storybook/react-vite';

import { ProgressBar } from '../src/shared/ui';

const meta = {
  title: 'Patterns/Progress bar',
  component: ProgressBar,
  tags: ['autodocs'],
  decorators: [
    (Story: () => React.ReactNode) => (
      <div className="w-panel">
        <Story />
      </div>
    ),
  ],
  args: { label: 'Прогресс прогона', value: 34, max: 120 },
} satisfies Meta<typeof ProgressBar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Progress: Story = {};
export const Capacity: Story = { args: { label: undefined, tone: 'danger', value: 5, max: 5 } };
export const Indeterminate: Story = { args: { value: 0, max: 0, indeterminate: true } };
export const Days: Story = { args: { label: undefined, segments: 42, value: 8, max: 15 } };
