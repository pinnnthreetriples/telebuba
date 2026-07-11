import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';
import { eventLabel, formatLocalTime, logSeverity } from '@/shared/lib';
import { CollapsibleCard } from '@/shared/ui';

// Activity-feed line colour by the event's display severity (see `logSeverity`).
const NEURO_LOG_COLOR: Record<'success' | 'warning' | 'error', string> = {
  success: '#7be0a6',
  warning: '#ffd27f',
  error: '#e5736b',
};

function extraStr(extra: LogEntry['extra'], key: string): string | undefined {
  const value = extra?.[key];
  return typeof value === 'string' ? value : undefined;
}

// One terminal line: time · channel · event · reason, with a hover hint (why + fix).
function LogLine({ line, t }: { line: LogEntry; t: TFunction }) {
  const channel = extraStr(line.extra, 'channel');
  // Most negative outcomes carry a `reason`; a failed post carries the Telegram `status`.
  const reasonCode = extraStr(line.extra, 'reason') ?? extraStr(line.extra, 'status');
  const detail = reasonCode ? t(`logEventReason.${reasonCode}`, { defaultValue: '' }) : '';
  const hint = t(`logEventHint.${line.event}`, { defaultValue: '' });
  return (
    <div className="flex gap-[10px]" title={hint || undefined}>
      <span className="shrink-0 text-[#5c5c66]">
        {formatLocalTime(line.created_at, { seconds: true })}
      </span>
      {channel ? <span className="shrink-0 text-[#6ea8fe]">{channel}</span> : null}
      <span style={{ color: NEURO_LOG_COLOR[logSeverity(line)] }}>{eventLabel(t, line.event)}</span>
      {detail ? <span className="truncate text-[#7a7a85]">· {detail}</span> : null}
    </div>
  );
}

// The neurocomment activity terminal — the tail of the live log stream.
export function ActivityLogCard({
  logLines,
  onClear,
}: {
  logLines: LogEntry[];
  onClear?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neurocomment.log.title')}
      headerClassName="px-4 py-[13px]"
      bodyClassName="px-[14px] pb-[14px]"
      trailing={
        onClear && logLines.length > 0 ? (
          <button
            type="button"
            aria-label={t('neurocomment.log.clear')}
            title={t('neurocomment.log.clear')}
            onClick={onClear}
            className="flex h-[28px] w-[28px] items-center justify-center rounded-lg border border-line bg-white text-ink-subtle hover:border-[#f0c9c5] hover:bg-danger-tint hover:text-danger"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M3 6h18" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>
        ) : undefined
      }
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
          logLines.map((line) => <LogLine key={line.id} line={line} t={t} />)
        )}
      </div>
    </CollapsibleCard>
  );
}
