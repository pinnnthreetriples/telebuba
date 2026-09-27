import type { ColumnDef } from '@tanstack/react-table';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { Badge, DataTable } from '../src/shared/ui';

type Account = { name: string; phone: string; state: string };

const data: Account[] = [
  { name: 'Иван Петров', phone: '+7 900 111-22-33', state: 'Прогрет' },
  { name: 'Мария Смирнова', phone: '+7 900 444-55-66', state: 'В прогреве' },
];
const columns: ColumnDef<Account>[] = [
  { id: 'name', header: 'Аккаунт', accessorKey: 'name' },
  { id: 'phone', header: 'Телефон', accessorKey: 'phone' },
  {
    id: 'state',
    header: 'Состояние',
    cell: ({ row }) => <Badge dot>{row.original.state}</Badge>,
  },
];

const meta = {
  title: 'Shared/DataTable',
  component: DataTable,
  tags: ['autodocs'],
  args: { data, columns },
} satisfies Meta<typeof DataTable<Account>>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
