import { useTranslation } from 'react-i18next';

import { Badge, Spinner, TerminalPane } from '@/shared/ui';

import type { ParsedUser, ParserMode } from '../model/userParser';

export type ParserLogLine = { at: string; tone: 'text' | 'success' | 'warning'; text: string };

const LINE_TONE = {
  text: 'text-term-text',
  success: 'text-term-success',
  warning: 'text-term-warning',
} as const;

const P = 'userParser.results';

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex flex-col">
      <span className="type-stat tabular-nums">{value}</span>
      <span className="type-caption">{label}</span>
    </div>
  );
}

type Props = {
  mode: ParserMode;
  running: boolean;
  done: number;
  total: number;
  // Сколько собрано до фильтров: рядом с итогом показывает, сколько отсеяли.
  raw: number;
  accountCount: number;
  log: ParserLogLine[];
  users: ParsedUser[];
};

export function UserParserResults({
  mode,
  running,
  done,
  total,
  raw,
  accountCount,
  log,
  users,
}: Props) {
  const { t, i18n } = useTranslation();
  const date = (iso: string) => new Date(iso).toLocaleDateString(i18n.language);

  return (
    <div className="flex flex-col gap-lg">
      <div className="flex flex-wrap items-end gap-page">
        <Stat
          value={`${String(Math.min(done, total))} / ${String(total)}`}
          label={t(`${P}.sources`)}
        />
        <Stat value={String(raw)} label={t(`${P}.raw`)} />
        <Stat value={String(users.length)} label={t(`${P}.users`)} />
        <Stat value={String(accountCount)} label={t(`${P}.accounts`)} />
        {running ? <Spinner /> : null}
      </div>

      <TerminalPane>
        {log.map((line, index) => (
          <div key={index} className="flex gap-tight">
            <span className="shrink-0 text-term-dim">{line.at}</span>
            <span className={LINE_TONE[line.tone]}>{line.text}</span>
          </div>
        ))}
      </TerminalPane>

      {users.length === 0 ? null : (
        <div className="flex flex-col gap-sm">
          <span className="type-label">{t(`${P}.title`, { count: users.length })}</span>
          {/* Строки, а не DataTable: в окне уже 880px таблица уходит в карточки, и 60
              карточек по шесть полей растягивают окно на десятки экранов. */}
          <ul className="tb-scroll max-h-dialog overflow-y-auto rounded-lg border border-line">
            {users.map((user) => (
              <li
                key={user.id}
                className="flex flex-wrap items-center gap-x-lg gap-y-xs border-t border-line-row px-lg py-sm first:border-t-0"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-xs type-label">
                    <span className="truncate">{user.name}</span>
                    {user.premium ? (
                      <Badge tone="warning" size="sm">
                        Premium
                      </Badge>
                    ) : null}
                  </div>
                  <div className="truncate type-caption">
                    {user.username ? `@${user.username}` : t(`${P}.noUsername`)} · {user.id}
                  </div>
                </div>
                {mode === 'members' ? null : (
                  <span className="type-caption">
                    {t(`${P}.count.${mode}`, { count: user.count })} · {date(user.firstAt)} —{' '}
                    {date(user.lastAt)}
                  </span>
                )}
                <span className="type-caption">{t(`${P}.lastSeen.${user.lastSeen}`)}</span>
                <span className="max-w-name truncate type-caption" title={user.sources.join(', ')}>
                  {user.sources.length > 1
                    ? t(`${P}.inSources`, { count: user.sources.length })
                    : user.sources[0]}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
