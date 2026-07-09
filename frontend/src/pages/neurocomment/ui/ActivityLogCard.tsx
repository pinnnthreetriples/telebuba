import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';
import { eventLabel, formatLocalTime } from '@/shared/lib';
import { CollapsibleCard } from '@/shared/ui';

// Activity-feed line colour by the real log row's status.
const NEURO_LOG_COLOR: Record<'success' | 'warning' | 'error', string> = {
  success: '#7be0a6',
  warning: '#ffd27f',
  error: '#e5736b',
};

// The neurocomment activity terminal — the tail of the live log stream.
export function ActivityLogCard({ logLines }: { logLines: LogEntry[] }) {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neurocomment.log.title')}
      headerClassName="px-4 py-[13px]"
      bodyClassName="px-[14px] pb-[14px]"
      header={
        <>
          <span className="pl-pulse h-[7px] w-[7px] shrink-0 rounded-full bg-primary" />
          <span className="text-[13px] font-semibold">{t('neurocomment.log.title')}</span>
          <span className="rounded-full bg-[#f2f1ee] px-2 py-[2px] text-[11px] font-medium text-ink-muted">
            {logLines.length}
          </span>
        </>
      }
    >
      <div className="term tb-scroll max-h-[220px] overflow-y-auto rounded-[10px] bg-[#16161a] px-[14px] py-3 font-mono text-[11px] leading-[1.85]">
        {logLines.length === 0 ? (
          <div className="text-[#5c5c66]">{t('neurocomment.log.empty')}</div>
        ) : (
          logLines.map((line) => (
            <div key={line.id} className="flex gap-[10px]">
              <span className="shrink-0 text-[#5c5c66]">
                {formatLocalTime(line.created_at, { seconds: true })}
              </span>
              <span style={{ color: NEURO_LOG_COLOR[line.status] }}>
                {eventLabel(t, line.event)}
              </span>
            </div>
          ))
        )}
      </div>
    </CollapsibleCard>
  );
}
