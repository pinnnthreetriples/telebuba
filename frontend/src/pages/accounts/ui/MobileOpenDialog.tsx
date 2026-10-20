import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountMobileProgressQueryOptions } from '@/entities/account';
import { PhoneScreen, PhoneScreenWaiting } from '@/features/phone-screen';
import type { MobileOpenProgress, OpenMobileResult } from '@/shared/api';
import { Modal, ModalHeader, Notice, Stepper, type StepperStep } from '@/shared/ui';

type MobileOpenStage = NonNullable<MobileOpenProgress['stage']>;

// While the open runs, the dialog asks the server where it is this often.
const POLL_MS = 1000;

// The stages a phone goes through on its way to the chat list, in order.
const STEPS = ['pod', 'boot', 'app', 'phone', 'code', 'password'] as const;
type Step = (typeof STEPS)[number];
// Before these there is no Android to show yet: the screen waits in its frame.
const NO_SCREEN: ReadonlySet<MobileOpenStage> = new Set(['pod', 'boot']);

export type MobileOutcome =
  | { phase: 'running' }
  | { phase: 'settled'; result: OpenMobileResult }
  | { phase: 'failed'; message: string };

function isStep(stage: MobileOpenStage): stage is Step {
  return (STEPS as readonly string[]).includes(stage);
}

// Where the dialog stands: the open's own answer once it has one; until then the
// server's latest stage, never an earlier open's ending (the poll may beat the start).
function currentStage(
  outcome: MobileOutcome,
  polled: MobileOpenStage | null | undefined,
): MobileOpenStage {
  if (outcome.phase === 'failed') return 'failed';
  if (outcome.phase === 'settled') return outcome.result.signed_in ? 'done' : 'manual';
  return polled && isStep(polled) ? polled : 'pod';
}

function steps(stage: MobileOpenStage, reached: Step, t: (key: string) => string): StepperStep[] {
  const at = STEPS.indexOf(reached);
  return STEPS.map((step, index) => ({
    id: step,
    label: t(`accounts.openMobile.stage.${step}`),
    // Where an open that ended short stopped stays marked; only a running one pulses.
    state: stage === 'done' || index < at ? 'done' : index === at ? 'current' : 'upcoming',
  }));
}

// The phone the operator opened from the accounts list, as it starts: the stages of
// its sign-in over its live screen. The screen only watches while the app signs itself
// in, and takes the mouse and keyboard once the open is over, so a sign-in that
// stopped short is finished by hand right here. Closing the dialog leaves the phone up.
export function MobileOpenDialog({
  accountId,
  title,
  outcome,
  onClose,
}: {
  accountId: string;
  title: string;
  outcome: MobileOutcome;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const running = outcome.phase === 'running';
  // Never kept once the dialog closes: a later open must not start from this one's stage
  // (the page also drops it when it starts an open).
  const progress = useQuery({
    ...accountMobileProgressQueryOptions({ path: { account_id: accountId } }),
    gcTime: 0,
    refetchInterval: running ? POLL_MS : false,
  });
  const polled = progress.data?.stage;
  const stage = currentStage(outcome, polled);
  // The furthest stage this open was seen in: the stepper keeps it once the open ends.
  const [reached, setReached] = useState<Step>('pod');
  if (polled && isStep(polled) && STEPS.indexOf(polled) > STEPS.indexOf(reached)) {
    setReached(polled);
  }
  const phoneId = progress.data?.phone_id;
  const refusal = outcome.phase === 'settled' ? outcome.result.sign_in_refusal : null;

  return (
    <Modal size="panel" label={title} onClose={onClose}>
      <ModalHeader title={title} subtitle={t('accounts.openMobile.subtitle')} />
      <div className="flex flex-col gap-4 p-6">
        <Stepper
          steps={steps(stage, reached, t)}
          pulse={running}
          narrow="current"
          label={t('accounts.openMobile.stepsLabel')}
        />
        <Notice
          tone={
            stage === 'done'
              ? 'success'
              : stage === 'failed'
                ? 'danger'
                : stage === 'manual'
                  ? 'warning'
                  : 'info'
          }
        >
          <span role="status">{t(`accounts.openMobile.status.${stage}`)}</span>
          {refusal && (
            <span className="mt-1 block">
              {t('accounts.openMobile.reason', {
                reason: t(`shell.code.${refusal}`, { defaultValue: refusal, s: '?' }),
              })}
            </span>
          )}
          {outcome.phase === 'failed' && (
            <span className="mt-1 block">
              {t('accounts.openMobile.reason', { reason: outcome.message })}
            </span>
          )}
        </Notice>
        {outcome.phase !== 'failed' &&
          (phoneId && !NO_SCREEN.has(stage) ? (
            // Keyed by mode: watching becomes controlling with a fresh stream.
            <PhoneScreen
              key={running ? 'view' : 'control'}
              phoneId={phoneId}
              mode={running ? 'view' : 'control'}
            />
          ) : (
            <PhoneScreenWaiting
              label={t(`accounts.openMobile.waiting.${stage === 'boot' ? 'boot' : 'pod'}`)}
            />
          ))}
      </div>
    </Modal>
  );
}
