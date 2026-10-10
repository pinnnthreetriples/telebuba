// Журнал прогона парсера — из того, что сервер знает о прогоне: статусы источников,
// аккаунты на паузе и итог. Отдельной ленты событий у окна нет: строки пересобираются при
// каждом обновлении прогона, поэтому журнал после перезагрузки окна тот же.
import type { TFunction } from 'i18next';

import type { UserParserRun, UserParserSourceReport } from '@/shared/api';

import { topicOf } from './userParser';

export type ParserLogLine = { at: string; tone: 'text' | 'success' | 'warning'; text: string };

const P = 'userParser.log';

const SOURCE_LINE: Record<
  Exclude<UserParserSourceReport['status'], 'pending' | undefined>,
  { tone: ParserLogLine['tone']; key: string }
> = {
  ok: { tone: 'success', key: 'found' },
  partial: { tone: 'warning', key: 'partial' },
  hidden: { tone: 'warning', key: 'hidden' },
  join_failed: { tone: 'warning', key: 'joinFailed' },
  flood: { tone: 'warning', key: 'sourceFlood' },
  failed: { tone: 'warning', key: 'sourceFailed' },
};

const END_LINE: Record<
  Exclude<UserParserRun['status'], 'running'>,
  { tone: ParserLogLine['tone']; key: string }
> = {
  done: { tone: 'success', key: 'done' },
  stopped: { tone: 'warning', key: 'stopped' },
  interrupted: { tone: 'warning', key: 'interrupted' },
  failed: { tone: 'warning', key: 'failed' },
};

export function runLog(run: UserParserRun, t: TFunction, locale: string): ParserLogLine[] {
  const time = (iso: string | null | undefined) =>
    iso ? new Date(iso).toLocaleTimeString(locale) : '';
  const lines: ParserLogLine[] = [
    {
      at: time(run.created_at),
      tone: 'text',
      text: t(`${P}.start`, {
        accounts: run.accounts?.length ?? 0,
        sources: run.sources_total ?? run.sources?.length ?? 0,
      }),
    },
  ];
  for (const source of run.sources ?? []) {
    const status = source.status ?? 'pending';
    if (status === 'pending') continue;
    const at = time(source.finished_at);
    const topic = run.mode === 'messages' ? topicOf(source.source) : null;
    lines.push({
      at,
      tone: 'text',
      text: t(`${P}.${topic === null ? 'chat' : 'topic'}`, { chat: source.source, topic }),
    });
    const line = SOURCE_LINE[status];
    lines.push({ at, tone: line.tone, text: t(`${P}.${line.key}`, { count: source.count ?? 0 }) });
  }
  for (const account of run.accounts ?? []) {
    if (account.state === 'flooded' || account.state === 'dropped') {
      const key = account.state === 'flooded' ? 'flood' : 'accountDropped';
      lines.push({ at: '', tone: 'warning', text: t(`${P}.${key}`, { name: account.name }) });
    }
  }
  if (run.status !== 'running') {
    const at = time(run.finished_at);
    lines.push({
      at,
      tone: 'success',
      text: t(`${P}.filtered`, { raw: run.collected_raw ?? 0, kept: run.kept ?? 0 }),
    });
    const end = END_LINE[run.status];
    lines.push({ at, tone: end.tone, text: t(`${P}.${end.key}`) });
  }
  return lines;
}
