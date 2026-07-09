import { useTranslation } from 'react-i18next';

import { Odometer } from './Odometer';

const STAGES = ['listen', 'detect', 'filter', 'generate', 'solve', 'comment'] as const;

interface Stat {
  label: string;
  value: number;
  color: string;
}

// The engine pipeline card: global start/stop, the six-stage stepper with the
// dual (green/blue) progress fill, a status banner, and the stat odometer grid.
export function PipelineCard({
  running,
  canStart,
  stats,
  onToggle,
}: {
  running: boolean;
  canStart: boolean;
  stats: Stat[];
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  // Decorative pipeline position: a mid-flight look while running, idle when off.
  const activeCell = running ? 2 : -1;
  const greenPct = activeCell > 0 ? (activeCell / (STAGES.length - 1)) * 100 : 0;
  const bluePct = activeCell >= 0 ? (activeCell / (STAGES.length - 1)) * 100 : 0;
  return (
    <div className="rounded-2xl border border-[#e4ecfa] bg-[#f7faff] px-[18px] py-4 text-ink">
      <div className="mb-[14px] flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-[10px]">
          <span className="text-[14px] font-semibold">{t('neurocomment.pipeline.title')}</span>
          <span
            className={`rounded-full px-[10px] py-[3px] text-[11px] font-semibold ${running ? 'tb-pulse bg-success-tint text-success' : 'bg-track text-ink-muted'}`}
          >
            {running ? t('neurocomment.pipeline.running') : t('neurocomment.pipeline.stopped')}
          </span>
        </div>
        <button
          type="button"
          disabled={!running && !canStart}
          onClick={onToggle}
          className={`flex items-center gap-[7px] rounded-full px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50 ${running ? 'bg-ink' : 'bg-primary'}`}
        >
          {running ? (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="5" width="4" height="14" rx="1.5" />
              <rect x="14" y="5" width="4" height="14" rx="1.5" />
            </svg>
          ) : (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
              <path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5z" />
            </svg>
          )}
          {running ? t('neurocomment.runtime.stop') : t('neurocomment.runtime.start')}
        </button>
      </div>

      {/* stepper with dual progress fill */}
      <div className="relative mx-2 mb-3 h-6">
        <div className="absolute inset-x-[13px] top-[11px] h-[2px] overflow-hidden rounded-[2px] bg-[#dce2ec]">
          <div
            className="absolute left-0 top-0 h-full rounded-[2px] bg-success transition-[width] duration-[900ms] [transition-timing-function:cubic-bezier(.16,1,.3,1)]"
            style={{ width: `${String(greenPct)}%` }}
          />
          <div
            className="absolute left-0 top-0 h-full rounded-[2px] bg-primary transition-[width] duration-[900ms] [transition-timing-function:cubic-bezier(.16,1,.3,1)]"
            style={{ width: `${String(bluePct)}%` }}
          />
        </div>
        <div className="relative flex h-6 items-center justify-between">
          {STAGES.map((stage, index) => (
            <div key={stage} className="relative flex h-4 w-4 shrink-0 items-center justify-center">
              {index < activeCell ? (
                <span className="tb-pop flex h-4 w-4 items-center justify-center rounded-full bg-success">
                  <svg
                    width="10"
                    height="10"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#fff"
                    strokeWidth="3.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                </span>
              ) : index === activeCell ? (
                <span className="tb-livedot h-[11px] w-[11px] rounded-full bg-primary" />
              ) : (
                <span className="h-[9px] w-[9px] rounded-full border-[1.5px] border-[#c9d2e0] bg-white" />
              )}
            </div>
          ))}
        </div>
      </div>
      <div className="mb-3 flex justify-between px-px">
        {STAGES.map((stage, index) => (
          <span
            key={stage}
            className={`w-[88px] whitespace-nowrap text-center text-[11px] ${
              index < activeCell
                ? 'font-medium text-success'
                : index === activeCell
                  ? 'font-semibold text-primary'
                  : 'text-ink-subtle'
            }`}
          >
            {t(`neurocomment.stage.${stage}`)}
          </span>
        ))}
      </div>

      <div className="mb-[14px] flex items-center gap-[9px] rounded-[10px] border border-[#dce7fb] bg-[#eef4ff] px-[13px] py-[10px]">
        <span className="pl-pulse h-2 w-2 shrink-0 rounded-full bg-primary" />
        <span className="tb-pulse text-[12.5px] font-medium text-primary">
          {running
            ? t('neurocomment.pipeline.descRunning')
            : t('neurocomment.pipeline.descStopped')}
        </span>
      </div>

      <div className="grid grid-cols-5 gap-px overflow-hidden rounded-xl border border-[#e4ecfa] bg-[#e4ecfa]">
        {stats.map((stat) => (
          <div key={stat.label} className="bg-white px-4 py-[14px]">
            <Odometer value={stat.value} color={stat.color} />
            <div className="mt-[2px] text-[11px] text-ink-subtle">{stat.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
