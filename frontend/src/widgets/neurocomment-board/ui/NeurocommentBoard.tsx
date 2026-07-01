import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChannelStatusBadge } from '@/entities/campaign';
import type {
  NeurocommentBoard as NeurocommentBoardData,
  NeurocommentChannelRow,
} from '@/shared/api';

interface BoardRow {
  account: string;
  channel: string;
  text: string;
  status: NeurocommentChannelRow['status'];
}

// One work row per account, joined on the account's OWN channel: its first
// joined channel from the readiness list (a real link, not an arbitrary pairing)
// with that channel's real aggregate status. The comment cell shows the account's
// real last comment text (falling back to a generic "posted" hint, then an em
// dash when it has never commented).
function deriveRows(board: NeurocommentBoardData, placeholder: string): BoardRow[] {
  const channelStatus = new Map((board.channels ?? []).map((c) => [c.channel, c.status]));
  return (board.accounts ?? []).map((account) => {
    const readiness = account.readiness ?? [];
    const primary = readiness.find((r) => r.joined) ?? readiness[0];
    const channel = primary?.channel ?? '—';
    return {
      account: account.label,
      channel,
      text: account.last_comment_text ?? (account.last_comment_at ? placeholder : '—'),
      status: channelStatus.get(channel) ?? 'comments_off',
    };
  });
}

// The design's "Доска работ" card: a collapsible header (account count pill,
// freshness, gear→neuro-accounts modal, chevron) over a 4-column work table.
export function NeurocommentBoard({
  board,
  accountsCount,
  onOpenAccounts,
}: {
  board: NeurocommentBoardData;
  accountsCount: number;
  onOpenAccounts: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const rows = deriveRows(board, t('neurocomment.board.commentPlaceholder'));

  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-white">
      <div className="flex items-center justify-between border-b border-[#f0eeeb] px-4 py-[14px]">
        <button
          type="button"
          onClick={() => {
            setOpen((v) => !v);
          }}
          className="flex items-center gap-2 text-left"
        >
          <span className="text-[13px] font-semibold">{t('neurocomment.board.title')}</span>
          <span className="rounded-full bg-primary-tint px-2 py-[2px] text-[11px] font-semibold text-primary">
            {t('neurocomment.board.accounts', { count: accountsCount })}
          </span>
        </button>
        <div className="flex items-center gap-[10px]">
          <span className="text-[11px] text-ink-muted">{t('neurocomment.board.updated')}</span>
          <button
            type="button"
            title={t('neurocomment.modal.neuroAccounts.title')}
            aria-label={t('neurocomment.modal.neuroAccounts.title')}
            onClick={onOpenAccounts}
            className="flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-white text-ink-muted transition-colors hover:border-[#cbd7ec] hover:bg-[#f2f6ff] hover:text-primary"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          <button
            type="button"
            aria-label={t('neurocomment.board.title')}
            onClick={() => {
              setOpen((v) => !v);
            }}
            className={`flex text-ink-subtle transition-transform duration-[420ms] [transition-timing-function:cubic-bezier(.34,1.45,.6,1)] ${open ? 'rotate-180' : ''}`}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
        </div>
      </div>
      <div className={`tb-collapse ${open ? 'tb-open' : ''}`}>
        <div className="tb-scroll overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse">
            <thead>
              <tr className="bg-[#faf9f7]">
                {(['account', 'channel', 'comment', 'status'] as const).map((key) => (
                  <th
                    key={key}
                    className="px-4 py-[10px] text-left text-[11px] font-medium uppercase tracking-[.04em] text-[#9a9893]"
                  >
                    {t(`neurocomment.board.col.${key}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.account}-${String(index)}`} className="border-t border-[#f0eeeb]">
                  <td className="whitespace-nowrap px-4 py-[11px] text-[12.5px] font-medium">
                    {row.account}
                  </td>
                  <td className="whitespace-nowrap px-4 py-[11px] text-[12.5px] text-primary">
                    {row.channel}
                  </td>
                  <td className="max-w-[240px] overflow-hidden text-ellipsis whitespace-nowrap px-4 py-[11px] text-[12.5px] text-[#5c5c5c]">
                    {row.text}
                  </td>
                  <td className="px-4 py-[11px]">
                    <ChannelStatusBadge status={row.status} />
                  </td>
                </tr>
              ))}
              {rows.length === 0 ? (
                <tr className="border-t border-[#f0eeeb]">
                  <td colSpan={4} className="px-4 py-8 text-center text-[12.5px] text-ink-subtle">
                    {t('neurocomment.board.empty')}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
