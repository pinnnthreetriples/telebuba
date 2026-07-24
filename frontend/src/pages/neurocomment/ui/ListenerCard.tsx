import { useTranslation } from 'react-i18next';

import { accountDisplayName } from '@/entities/account';
import type { AccountRead } from '@/shared/api';

import { SurfHover } from './SurfHover';

// The listener-account card: shows the active listener with pause/edit/remove
// actions (revealed via SurfHover), or a dropdown to choose one when none is set.
export function ListenerCard({
  listenerId,
  running,
  activeCampaignCount,
  listenerActionsOpen,
  onToggleActions,
  onToggleRuntime,
  onEdit,
  onRemove,
  listenerOpen,
  onToggleOpen,
  accountOptions,
  onPickListener,
}: {
  listenerId: string;
  running: boolean;
  activeCampaignCount: number;
  listenerActionsOpen: boolean;
  onToggleActions: () => void;
  onToggleRuntime: () => void;
  onEdit: () => void;
  onRemove: () => void;
  listenerOpen: boolean;
  onToggleOpen: () => void;
  accountOptions: AccountRead[];
  onPickListener: (accountId: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="relative z-[5] rounded-2xl border border-line bg-white px-[14px] py-[13px]">
      <div className="mb-[3px] flex items-center gap-[9px]">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary">
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M2 10v3" />
            <path d="M6 6v11" />
            <path d="M10 3v18" />
            <path d="M14 8v7" />
            <path d="M18 5v13" />
            <path d="M22 10v3" />
          </svg>
        </span>
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-ink">
            {t('neurocomment.listener.title')}
          </div>
        </div>
      </div>

      {listenerId ? (
        <div className="mt-[9px]">
          <SurfHover
            shift={144}
            surfaceId="lsn-surf"
            open={listenerActionsOpen}
            actions={
              <>
                <button
                  type="button"
                  title={
                    running ? t('neurocomment.listener.pause') : t('neurocomment.listener.resume')
                  }
                  onClick={onToggleRuntime}
                  className={`flex w-12 items-center justify-center border-none bg-transparent ${running ? 'text-[#c47d12]' : 'text-success'}`}
                >
                  {running ? (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                      <rect x="6" y="5" width="4" height="14" rx="1" />
                      <rect x="14" y="5" width="4" height="14" rx="1" />
                    </svg>
                  ) : (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5z" />
                    </svg>
                  )}
                </button>
                <button
                  type="button"
                  title={t('neurocomment.listener.edit')}
                  onClick={onEdit}
                  className="flex w-12 items-center justify-center border-none bg-transparent text-primary"
                >
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                  >
                    <path d="M12 20h9" />
                    <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                  </svg>
                </button>
                <button
                  type="button"
                  title={t('neurocomment.listener.remove')}
                  onClick={onRemove}
                  className="flex w-12 items-center justify-center border-none bg-transparent text-danger"
                >
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                  >
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                  </svg>
                </button>
              </>
            }
            surface={
              <div
                className="flex items-center justify-between gap-2 rounded-[10px] border px-[10px] py-2"
                style={{
                  background: running ? '#ddf7e9' : '#f7f6f4',
                  borderColor: running ? '#b8ecce' : '#e6e5e3',
                }}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${running ? 'tb-livedot' : ''}`}
                    style={{ background: running ? '#12a150' : '#9a9893' }}
                  />
                  <span
                    className={`text-[12.5px] font-semibold ${running ? 'tb-pulse' : ''}`}
                    style={{ color: running ? '#12a150' : '#74726e' }}
                  >
                    {running
                      ? t('neurocomment.listener.listening')
                      : t('neurocomment.listener.paused')}
                  </span>
                  <span
                    title={t('neurocomment.listener.activeCampaigns')}
                    className="inline-flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-full px-[5px] text-[10.5px] font-bold text-white"
                    style={{ background: running ? '#12a150' : '#74726e' }}
                  >
                    {activeCampaignCount}
                  </span>
                </div>
                <button
                  type="button"
                  title={t('neurocomment.listener.actions')}
                  aria-label={t('neurocomment.listener.actions')}
                  aria-expanded={listenerActionsOpen}
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggleActions();
                  }}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[7px] border border-line bg-white text-ink-subtle transition-colors hover:border-[#cbd7ec] hover:bg-[#f2f6ff] hover:text-primary"
                >
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <circle cx="12" cy="12" r="3" />
                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                  </svg>
                </button>
              </div>
            }
          />
        </div>
      ) : (
        <div className="relative mt-[9px]">
          <button
            type="button"
            onClick={onToggleOpen}
            className="tb-time flex w-full items-center justify-between rounded-[10px] border border-line-input bg-white px-[13px] py-[10px] text-[13px]"
          >
            <span className="text-ink-subtle">{t('neurocomment.listener.choose')}</span>
            <span
              className={`tb-ddchev flex shrink-0 text-ink-subtle ${listenerOpen ? 'open' : ''}`}
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="m6 9 6 6 6-6" />
              </svg>
            </span>
          </button>
          <div
            className={`tb-dd absolute inset-x-0 top-[calc(100%+5px)] z-20 rounded-[10px] border border-line bg-white p-1 shadow-[0_10px_30px_rgba(11,11,12,0.1)] ${listenerOpen ? 'open' : ''}`}
          >
            {accountOptions.length === 0 ? (
              <div className="px-[10px] py-2 text-[12.5px] text-ink-subtle">
                {t('neurocomment.listener.noAccounts')}
              </div>
            ) : (
              accountOptions.map((account) => (
                <button
                  key={account.account_id}
                  type="button"
                  onClick={() => {
                    onPickListener(account.account_id);
                  }}
                  className="flex w-full items-center justify-between gap-2 rounded-[7px] px-[10px] py-2 text-left text-[12.5px] transition-colors hover:bg-[#f2f6ff]"
                >
                  <span className="font-medium">{accountDisplayName(account)}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
