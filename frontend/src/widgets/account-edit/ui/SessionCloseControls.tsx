import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountSessionAuditQueryKey,
  closeAccountSessionsMutation,
  invalidateAccountViews,
} from '@/entities/account';
import type { AccountSession, SessionCloseOutcome, SessionCloseResult } from '@/shared/api';
import { Button, ConfirmModal, Notice } from '@/shared/ui';

import { isRefused, sessionOutcomeText } from './_sessionOutcome';

// «Close foreign sessions»: the one place on the card that ends anything. The dialog lists
// exactly the sessions that will be closed — the hashes it sends are those on screen, and
// the server reads Telegram's list again and refuses whatever stopped being foreign. Ours
// are never in the list and cannot be named. The answer is one line per session that was
// not simply closed, because a partial success (Telegram refuses a session younger than a
// day to end others) must be told as it happened.

function OutcomeLine({ outcome }: { outcome: SessionCloseOutcome }) {
  const { t } = useTranslation();
  return <li>{sessionOutcomeText(t, outcome)}</li>;
}

export function SessionCloseControls({
  accountId,
  foreign,
}: {
  accountId: string;
  foreign: AccountSession[];
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<SessionCloseResult | null>(null);
  const close = useMutation(closeAccountSessionsMutation());
  const path = { path: { account_id: accountId } };

  const run = () =>
    close
      .mutateAsync({ ...path, body: { hashes: foreign.map((session) => session.hash) } })
      .then((answer) => {
        setResult(answer);
        if (answer.audit) queryClient.setQueryData(accountSessionAuditQueryKey(path), answer.audit);
      })
      .finally(() => {
        invalidateAccountViews(queryClient);
      });

  const refused = result?.outcomes.filter(isRefused);

  return (
    <>
      {foreign.length > 0 ? (
        <Button
          variant="danger"
          disabled={close.isPending}
          onClick={() => {
            setConfirming(true);
          }}
        >
          {t('accounts.edit.sessionsClose')}
        </Button>
      ) : null}
      {result ? (
        <div className="basis-full">
          <Notice tone={refused?.length ? 'warning' : 'success'} role="status">
            <div>{t('accounts.edit.sessionsClosed', { count: result.closed_count })}</div>
            {refused?.length ? (
              <ul className="mt-1 list-disc pl-4">
                {refused.map((outcome) => (
                  <OutcomeLine key={outcome.hash} outcome={outcome} />
                ))}
              </ul>
            ) : null}
          </Notice>
        </div>
      ) : null}
      {confirming ? (
        <ConfirmModal
          title={t('accounts.edit.sessionsCloseTitle')}
          body={
            <>
              <p className="mb-2">{t('accounts.edit.sessionsCloseBody')}</p>
              <ul className="list-disc pl-4">
                {foreign.map((session) => (
                  <li key={session.hash}>
                    {[session.device_model, session.app_name, session.ip, session.country]
                      .filter(Boolean)
                      .join(' · ')}
                  </li>
                ))}
              </ul>
            </>
          }
          confirmLabel={t('accounts.edit.sessionsClose')}
          cancelLabel={t('accounts.edit.cancel')}
          onClose={() => {
            setConfirming(false);
          }}
          onConfirm={run}
        />
      ) : null}
    </>
  );
}
