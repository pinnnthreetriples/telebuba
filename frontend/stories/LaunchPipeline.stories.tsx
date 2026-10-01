import type { Meta, StoryObj } from '@storybook/react-vite';

import type {
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingRole,
  NeuroshillingStep,
} from '../src/shared/api';
import { PipelineCard } from '../src/pages/neuroshilling/ui/PipelineCard';

const campaign: NeuroshillingCampaign = {
  campaign_id: 'c1',
  name: 'Запуск токена',
  mode: 'campaign',
  scenario_status: 'approved',
  run_mode: 'sequential',
  status: 'idle',
  created_at: '2026-08-28T12:00:00Z',
  updated_at: '2026-08-28T12:00:00Z',
};

const roles: NeuroshillingRole[] = [
  { role_id: 'r1', name: 'Скептик', created_at: '2026-08-28T12:00:00Z' },
  { role_id: 'r2', name: 'Сторонник', created_at: '2026-08-28T12:00:00Z' },
];

const steps: NeuroshillingStep[] = [
  {
    step_id: 's1',
    position: 1,
    kind: 'message',
    role_id: 'r1',
    delay_min_seconds: 60,
    delay_max_seconds: 180,
  },
  {
    step_id: 's2',
    position: 2,
    kind: 'message',
    role_id: 'r2',
    delay_min_seconds: 30,
    delay_max_seconds: 90,
  },
];

const pool: NeuroshillingBoardAccount[] = [
  { account_id: 'a1', title: 'Иван Петров', assigned: true, role_id: 'r1' },
  { account_id: 'a2', title: 'Мария Смирнова', assigned: true, role_id: 'r2' },
];

const meta = {
  title: 'Design System/Patterns/Launch readiness',
  component: PipelineCard,
  tags: ['autodocs'],
  args: {
    campaign,
    run: { status: 'idle', sent: 0, total: 120 },
    pool,
    targets: ['@crypto_daily', '@defi_news'],
    roles,
    steps,
    onStart: () => undefined,
    onStop: () => undefined,
    busy: false,
  },
} satisfies Meta<typeof PipelineCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Ready: Story = {};
export const Running: Story = { args: { run: { status: 'running', sent: 34, total: 120 } } };
export const Blocked: Story = {
  args: { campaign: { ...campaign, scenario_status: 'draft' } },
};
