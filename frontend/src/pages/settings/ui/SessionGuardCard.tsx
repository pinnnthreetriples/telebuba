import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { sessionGuardStatusQueryOptions } from '@/entities/account';
import type { SessionBackupStatus } from '@/shared/api';
import { formatLocalDateTime } from '@/shared/lib';
import { Card, Notice } from '@/shared/ui';

// How often the card asks again: the guard itself makes a pass every five minutes.
const REFRESH_MS = 60_000;

type RowTone = 'quiet' | 'warning' | 'danger';
const ROW_CLASS: Record<RowTone, string> = {
  quiet: 'text-content-subtle',
  warning: 'text-warning-deep font-medium',
  danger: 'text-danger-deep font-medium',
};

const VAULT_TONE: Record<NonNullable<SessionBackupStatus['passkey_vault']>, RowTone> = {
  off: 'quiet',
  ok: 'quiet',
  sealed: 'warning',
  unreachable: 'danger',
  rejected: 'danger',
};

const K = 'settings.sessionGuard.backups';

// "Copies and vault": the safety nets behind the sessions. Healthy rows stay quiet; a row
// that needs the operator turns warning or danger.
function BackupsBlock({ backups, language }: { backups: SessionBackupStatus; language: string }) {
  const { t } = useTranslation();
  const when = (iso: string | null | undefined) =>
    iso ? formatLocalDateTime(Date.parse(iso), language) : null;
  const copiesOn = backups.session_vault_enabled === true;
  const lastCopy = when(backups.last_session_copy_at);
  const lastDb = when(backups.last_db_backup_at);
  const vault = backups.passkey_vault ?? 'off';
  const without = backups.accounts_without_copy ?? 0;

  const rows: { key: string; tone: RowTone; text: string }[] = [
    {
      key: 'copies',
      tone: copiesOn ? 'quiet' : 'warning',
      text: t(copiesOn ? `${K}.copiesOn` : `${K}.copiesOff`),
    },
  ];
  if (copiesOn && without > 0) {
    rows.push({
      key: 'without',
      tone: 'warning',
      text: t(`${K}.withoutCopy`, { n: without, total: backups.accounts_total ?? 0 }),
    });
  }
  if (copiesOn) {
    rows.push({
      key: 'lastCopy',
      tone: 'quiet',
      text: lastCopy ? t(`${K}.lastCopy`, { date: lastCopy }) : t(`${K}.noCopyYet`),
    });
    rows.push({
      key: 'mirror',
      tone: 'quiet',
      text: t(backups.mirror_configured ? `${K}.mirrorOn` : `${K}.mirrorOff`),
    });
  }
  if (backups.db_backup_enabled) {
    rows.push({
      key: 'db',
      tone: 'quiet',
      text: lastDb ? t(`${K}.lastDb`, { date: lastDb }) : t(`${K}.noDbYet`),
    });
    if (backups.db_backup_overdue) {
      rows.push({ key: 'dbOverdue', tone: 'warning', text: t(`${K}.dbOverdue`) });
    }
    const mirrored = when(backups.db_mirror_last_at);
    rows.push({
      key: 'dbMirror',
      tone: 'quiet',
      text: !backups.db_mirror_enabled
        ? t(`${K}.dbMirrorOff`)
        : mirrored
          ? t(`${K}.dbMirrorLast`, { date: mirrored })
          : t(`${K}.dbMirrorNone`),
    });
  } else {
    rows.push({ key: 'db', tone: 'quiet', text: t(`${K}.dbOff`) });
  }
  rows.push({ key: 'vault', tone: VAULT_TONE[vault], text: t(`${K}.vault.${vault}`) });

  return (
    <div className="mt-4 border-t border-canvas pt-3" data-testid="session-guard-backups">
      <p className="m-0 mb-2 type-body-medium">{t(`${K}.title`)}</p>
      <ul className="m-0 list-none space-y-1 p-0">
        {rows.map((row) => (
          <li key={row.key} className={`type-small ${ROW_CLASS[row.tone]}`}>
            {row.text}
          </li>
        ))}
      </ul>
    </div>
  );
}

// The session guard's heartbeat. It reads the fleet's sessions and catches a dead key; it
// once went silent for 41 hours unnoticed. The backend's watchdog already tells the bot
// when it stalls; this card lets the operator see it at a glance too.
export function SessionGuardCard() {
  const { t, i18n } = useTranslation();
  const status = useQuery({ ...sessionGuardStatusQueryOptions(), refetchInterval: REFRESH_MS });
  const view = status.data;
  const last = view?.last_activity_at
    ? formatLocalDateTime(Date.parse(view.last_activity_at), i18n.language)
    : null;

  return (
    <Card title={t('settings.sessionGuard.title')} subtitle={t('settings.sessionGuard.hint')}>
      {!view ? null : view.stalled ? (
        <Notice tone="danger" role="alert">
          {view.reason === 'stopped'
            ? t('settings.sessionGuard.stopped')
            : t('settings.sessionGuard.silent', { date: last ?? '—' })}
        </Notice>
      ) : (
        <p className="type-body text-content-subtle" role="status">
          {view.running
            ? t('settings.sessionGuard.running', { date: last ?? '—' })
            : t('settings.sessionGuard.notRunning')}
        </p>
      )}
      {view?.backups ? <BackupsBlock backups={view.backups} language={i18n.language} /> : null}
    </Card>
  );
}
