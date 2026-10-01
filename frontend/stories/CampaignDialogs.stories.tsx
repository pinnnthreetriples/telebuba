import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AdvancedLimitsModal } from '../src/pages/neuroshilling/ui/AdvancedLimitsModal';
import { ApproveModal } from '../src/pages/neuroshilling/ui/ApproveModal';
import { CampaignDetailsModal } from '../src/pages/neuroshilling/ui/CampaignDetailsModal';
import { CampaignSettingsModal } from '../src/pages/neuroshilling/ui/CampaignSettingsModal';
import { MediaModal } from '../src/pages/neuroshilling/ui/MediaModal';
import { draftOf } from '../src/pages/neuroshilling/ui/scenarioDraft';
import { setupDraftOf } from '../src/pages/neuroshilling/ui/setupDraft';
import { Button, FormField, SegmentedControl } from '../src/shared/ui';

import { board, campaign, scenario } from './campaignFixtures';

type DialogKind = 'details' | 'settings' | 'limits' | 'media' | 'approve';
type DialogState = 'default' | 'empty' | 'busy' | 'conflict' | 'draft';

function CampaignDialog({ kind, state }: { kind: DialogKind; state: DialogState }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const [setup, setSetup] = useState(() => setupDraftOf(campaign));
  const [draft, setDraft] = useState(() => draftOf(campaign, scenario));
  const [delays, setDelays] = useState(() =>
    (scenario.steps ?? []).map((step) => ({
      min: step.delay_min_seconds ?? 0,
      max: step.delay_max_seconds ?? 0,
    })),
  );
  const close = () => setOpen(false);
  const steps = state === 'empty' ? [] : (scenario.steps ?? []);
  const roles = state === 'empty' ? [] : (scenario.roles ?? []);
  return (
    <div className="min-h-screen bg-canvas p-lg">
      <Button fullWidth className="min-w-0" onClick={() => setOpen(true)}>
        <span className="min-w-0 truncate">
          {t('neuroshilling.settings.title', { name: campaign.name })}
        </span>
      </Button>
      {open && kind === 'details' && (
        <CampaignDetailsModal
          campaign={campaign}
          pool={state === 'empty' ? [] : (board.available ?? [])}
          targets={state === 'empty' ? [] : (board.targets ?? [])}
          roles={roles}
          steps={steps}
          run={board.run ?? {}}
          onOpenSettings={close}
          onClose={close}
        />
      )}
      {open && kind === 'settings' && (
        <CampaignSettingsModal
          name={campaign.name}
          dirty
          busy={state === 'busy'}
          saving={state === 'busy'}
          conflict={state === 'conflict'}
          onSave={close}
          onClose={close}
        >
          <FormField
            label={t('neuroshilling.scenario.topic.label')}
            field={{
              name: 'topic',
              state: { value: draft.topic, meta: { isTouched: false, errors: [] } },
              handleChange: (topic) => setDraft({ ...draft, topic }),
              handleBlur: () => {},
            }}
          />
          <SegmentedControl
            value={setup.runMode}
            onChange={(runMode) => setSetup({ ...setup, runMode })}
            options={[
              { value: 'sequential', label: t('neuroshilling.setup.runMode.sequential.title') },
              { value: 'parallel', label: t('neuroshilling.setup.runMode.parallel.title') },
            ]}
            ariaLabel={t('neuroshilling.setup.runMode.label')}
          />
        </CampaignSettingsModal>
      )}
      {open && kind === 'limits' && (
        <AdvancedLimitsModal
          draft={setup}
          onDraft={setSetup}
          reserveCount={state === 'empty' ? 0 : 2}
          live={state === 'busy'}
          onClose={close}
        />
      )}
      {open && kind === 'media' && (
        <MediaModal
          draft={state === 'empty' ? { ...draft, steps: [] } : draft}
          onDraft={setDraft}
          onClose={close}
        />
      )}
      {open && kind === 'approve' && (
        <ApproveModal
          roles={roles}
          steps={steps}
          status={state === 'draft' ? 'draft' : 'approved'}
          dirty={state === 'conflict'}
          onRegenerate={close}
          onApprove={close}
          onClose={close}
          delays={delays}
          onDelay={(index, min, max) =>
            setDelays((current) =>
              current.map((delay, at) => (at === index ? { min, max } : delay)),
            )
          }
          busy={state === 'busy'}
        />
      )}
    </div>
  );
}

const meta = {
  title: 'Design System/Patterns/Campaign dialogs',
  component: CampaignDialog,
  parameters: {
    layout: 'fullscreen',
    docs: {
      description: {
        component:
          'Настоящие диалоги кампании. Черновики меняются только в памяти этой истории; кнопки закрывают окно без API. Длинное имя и реплики проверяют переносы на узком экране.',
      },
    },
  },
  args: { kind: 'details', state: 'default' },
  argTypes: {
    kind: { control: false },
    state: { control: 'select', options: ['default', 'empty', 'busy', 'conflict', 'draft'] },
  },
  render: (args) => <CampaignDialog key={`${args.kind}:${args.state}`} {...args} />,
} satisfies Meta<typeof CampaignDialog>;
export default meta;
type Story = StoryObj<typeof meta>;
export const CampaignDetails: Story = {};
export const CampaignSettings: Story = { args: { kind: 'settings' } };
export const AdvancedLimits: Story = { args: { kind: 'limits' } };
export const Media: Story = { args: { kind: 'media' } };
export const Approve: Story = { args: { kind: 'approve' } };
export const ApproveDraft: Story = { args: { kind: 'approve', state: 'draft' } };
