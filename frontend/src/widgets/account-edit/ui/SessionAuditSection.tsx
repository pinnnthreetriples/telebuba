import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountRekeyQueryOptions,
  accountSessionAuditQueryKey,
  accountSessionAuditQueryOptions,
  auditAccountSessionsMutation,
  invalidateAccountViews,
} from '@/entities/account';
import type { AccountRead, AccountSession } from '@/shared/api';
import { cn, formatLocalDateTime } from '@/shared/lib';
import { Badge, Button, Notice, Spinner } from '@/shared/ui';

import { Section } from './_shared';
import { SessionCloseControls } from './SessionCloseControls';
import { SessionRekeyControls } from './SessionRekeyControls';

// The sessions Telegram lists for the account: ours (the sign-ins this app holds) and
// everyone else's. A session that is not ours means somebody else holds the account, so
// the card turns into a warning. The list is the last saved audit; «Check sessions» reads Telegram again, and the accounts table's
// badge follows the same number. Ending a foreign session is SessionCloseControls.
// Ours and foreign must not look alike: a foreign row is red with a bar and a «Foreign»
// badge, ours is green with «Ours», so the operator reads the verdict before the details.
function SessionRow({ session }: { session: AccountSession }) {
  const { t, i18n } = useTranslation();
  const when = (iso: string) => formatLocalDateTime(Date.parse(iso), i18n.language);
  const isWeb = session.kind === 'web';
  const device = isWeb
    ? t('accounts.edit.sessionsWebTelebuba')
    : [session.device_model, session.app_name].filter(Boolean).join(' · ');
  const system = [session.platform, session.system_version].filter(Boolean).join(' ');
  const place = [session.ip, session.country].filter(Boolean).join(' · ');
  const times = [
    session.date_active
      ? t('accounts.edit.sessionsActive', { date: when(session.date_active) })
      : '',
    session.date_created
      ? t('accounts.edit.sessionsCreated', { date: when(session.date_created) })
      : '',
  ].filter(Boolean);
  return (
    <li
      className={cn(
        'flex flex-col gap-1 rounded-md border border-l-4 px-3 py-3',
        session.is_ours
          ? 'border-success-line border-l-success-deep bg-success-tint'
          : 'border-danger-line border-l-danger-deep bg-danger-tint',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="type-body-medium">{device || '—'}</span>
        <Badge tone={session.is_ours ? 'success' : 'danger'}>
          {session.is_ours
            ? t('accounts.edit.sessionsOursBadge')
            : t('accounts.edit.sessionsForeignRowBadge')}
        </Badge>
        {session.is_ours && !isWeb ? (
          <Badge tone="neutral">{t('accounts.edit.sessionsFingerprint')}</Badge>
        ) : null}
        {session.is_current ? (
          <Badge tone="info">{t('accounts.edit.sessionsCurrent')}</Badge>
        ) : null}
        {session.official_app ? (
          <Badge tone="neutral">{t('accounts.edit.sessionsOfficial')}</Badge>
        ) : null}
        {session.unconfirmed ? (
          <Badge tone="warning">{t('accounts.edit.sessionsUnconfirmed')}</Badge>
        ) : null}
      </div>
      {system ? <div className="type-small text-content-subtle">{system}</div> : null}
      {place ? <div className="type-small text-content-subtle">{place}</div> : null}
      {times.length > 0 ? (
        <div className="type-small text-content-subtle">{times.join(' · ')}</div>
      ) : null}
      {session.key_predates_import ? (
        <div className="type-small text-warning-deep">{t('accounts.edit.sessionsKeyPredates')}</div>
      ) : null}
    </li>
  );
}

function SessionGroup({
  title,
  explain,
  sessions,
}: {
  title: string;
  explain: string;
  sessions: AccountSession[];
}) {
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center gap-2">
        <span className="type-small-medium">{title}</span>
        <Badge tone="neutral" size="sm">
          {sessions.length}
        </Badge>
      </div>
      <p className="mb-2 type-small text-content-subtle">{explain}</p>
      <ul className="flex flex-col gap-2">
        {sessions.map((session) => (
          <SessionRow key={session.hash} session={session} />
        ))}
      </ul>
    </div>
  );
}

export function SessionAuditSection({ account }: { account: AccountRead }) {
  const { t, i18n } = useTranslation();
  const accountId = account.account_id;
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const path = { path: { account_id: accountId } };

  // A DB read only, but gated like the other cards so a closed card costs nothing.
  const saved = useQuery({ ...accountSessionAuditQueryOptions(path), enabled: open });
  const check = useMutation(auditAccountSessionsMutation());
  // Never re-keyed: the key in use may be the one whoever sold the account made.
  const rekey = useQuery({ ...accountRekeyQueryOptions(path), enabled: open });
  const neverRekeyed = rekey.isSuccess && !rekey.data.rekeyed_at;

  const view = saved.data;
  // The header speaks before the card is opened: the list already carries the count.
  const foreign = view ? view.foreign_count : account.foreign_sessions;
  const ours = view?.sessions.filter((session) => session.is_ours) ?? [];
  const others = view?.sessions.filter((session) => !session.is_ours) ?? [];
  const checkedAt = view?.checked_at ?? null;
  const hasProxy = Boolean(account.proxy_id);
  const addedAt = Date.parse(account.created_at);
  const sellerKey = ours.some((session) => session.key_predates_import);

  const recheck = () => {
    check.mutate(path, {
      onSuccess: (audit) => {
        queryClient.setQueryData(accountSessionAuditQueryKey(path), audit);
      },
      onSettled: () => {
        invalidateAccountViews(queryClient);
      },
    });
  };

  return (
    <Section
      title={t('accounts.edit.sessions')}
      onOpenChange={setOpen}
      right={
        saved.isFetching || check.isPending ? (
          <Spinner />
        ) : foreign == null ? (
          <Badge tone="neutral" size="sm">
            {t('accounts.edit.sessionsUnknown')}
          </Badge>
        ) : foreign > 0 ? (
          <Badge tone="warning" size="sm" dot>
            {t('accounts.edit.sessionsForeignBadge', { count: foreign })}
          </Badge>
        ) : (
          <Badge tone="success" size="sm" dot>
            {t('accounts.edit.sessionsOursOnly')}
          </Badge>
        )
      }
    >
      <p className="mb-3 type-body text-content-subtle">{t('accounts.edit.sessionsHint')}</p>
      {!hasProxy ? (
        <div className="mb-3">
          <Notice tone="warning" className="py-3 text-small font-medium">
            {t('accounts.edit.sessionsNoProxy')}
          </Notice>
        </div>
      ) : null}
      {view && view.foreign_count > 0 ? (
        <div className="mb-3">
          <Notice tone="warning" role="alert" className="py-3 text-small font-medium">
            {t('accounts.edit.sessionsWarning', { count: view.foreign_count })}
          </Notice>
        </div>
      ) : null}
      {view && checkedAt ? (
        <>
          {others.length > 0 ? (
            <SessionGroup
              title={t('accounts.edit.sessionsForeignTitle')}
              explain={t('accounts.edit.sessionsForeignExplain')}
              sessions={others}
            />
          ) : (
            <p className="mb-3 type-body text-content-subtle">
              {t('accounts.edit.sessionsNoForeign')}
            </p>
          )}
          <SessionGroup
            title={t('accounts.edit.sessionsOursTitle')}
            explain={t('accounts.edit.sessionsOursExplain')}
            sessions={ours}
          />
          {neverRekeyed && ours.length > 0 && !sellerKey ? (
            <div className="mb-3">
              <Notice tone="info" className="py-3 text-small">
                {t('accounts.edit.sessionsOursImported')}
              </Notice>
            </div>
          ) : null}
          {Number.isFinite(addedAt) ? (
            <p className="mb-3 type-small text-content-subtle">
              {t('accounts.edit.sessionsAddedToTelebuba', {
                date: formatLocalDateTime(addedAt, i18n.language),
              })}
            </p>
          ) : null}
          {view.ttl_days ? (
            <p className="mb-3 type-small text-content-subtle">
              {t('accounts.edit.sessionsTtl', { days: view.ttl_days })}
            </p>
          ) : null}
        </>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={!hasProxy || check.isPending || saved.isPending} onClick={recheck}>
          {t('accounts.edit.sessionsCheck')}
        </Button>
        <SessionCloseControls accountId={accountId} foreign={others} />
        {view ? (
          <span className="type-small text-content-subtle">
            {checkedAt
              ? t('accounts.edit.sessionsLastChecked', {
                  date: formatLocalDateTime(Date.parse(checkedAt), i18n.language),
                })
              : t('accounts.edit.sessionsNeverChecked')}
          </span>
        ) : null}
      </div>
      <SessionRekeyControls accountId={accountId} open={open} hasProxy={hasProxy} />
    </Section>
  );
}
