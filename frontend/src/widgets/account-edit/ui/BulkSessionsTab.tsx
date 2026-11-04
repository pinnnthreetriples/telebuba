import { useTranslation } from 'react-i18next';

import { Icon, Notice } from '@/shared/ui';

// Закрыть чужие сессии: for each account of the batch, the sessions that are foreign RIGHT
// NOW (the server reads Telegram's list again) are ended, one at a time with pauses. The
// operator confirms with the checkbox because, unlike the profile tabs, this one cannot be
// put back: a session that was ended stays ended.
export function BulkSessionsTab({
  kind = 'sessions',
  confirmed,
  onConfirmed,
}: {
  // The same confirm-and-run panel serves closing foreign sessions and re-keying.
  kind?: 'sessions' | 'rekey';
  confirmed: boolean;
  onConfirmed: (next: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      <Notice tone="warning">
        {t(`accounts.bulk.${kind === 'rekey' ? 'rekeyHint' : 'sessionsHint'}`)}
      </Notice>
      <button
        type="button"
        role="checkbox"
        aria-checked={confirmed}
        onClick={() => {
          onConfirmed(!confirmed);
        }}
        className="flex items-center gap-3 rounded-md border border-line px-4 py-3 text-left"
      >
        <span
          className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${confirmed ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
        >
          {confirmed && <Icon name="check" size={14} className="stroke-on-fill" />}
        </span>
        <span className="type-h3">
          {t(`accounts.bulk.${kind === 'rekey' ? 'rekeyConfirm' : 'sessionsConfirm'}`)}
        </span>
      </button>
    </div>
  );
}
