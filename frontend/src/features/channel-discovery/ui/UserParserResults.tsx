import { useTranslation } from 'react-i18next';

import { Badge, Spinner, StatGrid, TerminalPane } from '@/shared/ui';

import type { ParsedUser, ParserMode } from '../model/userParser';

export type ParserLogLine = { at: string; tone: 'text' | 'success' | 'warning'; text: string };

const LINE_TONE = {
  text: 'text-term-text',
  success: 'text-term-success',
  warning: 'text-term-warning',
} as const;

const P = 'userParser.results';

// Один пользователь — одна строка. Общая для итогов прогона и вкладки «Базы».
// Строки, а не DataTable: в окне уже 880px таблица уходит в карточки, и 60 карточек по
// шесть полей растягивают окно на десятки экранов.
export function UserRows({ mode, users }: { mode: ParserMode; users: ParsedUser[] }) {
  const { t, i18n } = useTranslation();
  const date = (iso: string) => new Date(iso).toLocaleDateString(i18n.language);
  return (
    <ul className="tb-scroll max-h-dialog overflow-y-auto rounded-lg border border-line">
      {users.map((user) => (
        <li
          key={user.id}
          className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-canvas px-4 py-2 first:border-t-0"
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1 type-body-medium">
              <span className="truncate">{user.name}</span>
              {user.premium ? (
                <Badge tone="warning" size="sm">
                  Premium
                </Badge>
              ) : null}
            </div>
            <div className="truncate type-small">
              {user.username ? `@${user.username}` : t(`${P}.noUsername`)} · {user.id}
            </div>
          </div>
          {mode === 'members' ? null : (
            <span className="type-small">
              {t(`${P}.count.${mode}`, { count: user.count })} · {date(user.firstAt)} —{' '}
              {date(user.lastAt)}
            </span>
          )}
          <span className="type-small">{t(`${P}.lastSeen.${user.lastSeen}`)}</span>
          <span className="max-w-name truncate type-small" title={user.sources.join(', ')}>
            {user.sources.length > 1
              ? t(`${P}.inSources`, { count: user.sources.length })
              : user.sources[0]}
          </span>
        </li>
      ))}
    </ul>
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
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <StatGrid
          className="flex-1"
          stats={[
            {
              label: t(`${P}.sources`),
              value: `${String(Math.min(done, total))} / ${String(total)}`,
            },
            { label: t(`${P}.raw`), value: raw },
            { label: t(`${P}.users`), value: users.length, tone: 'success' },
            { label: t(`${P}.accounts`), value: accountCount },
          ]}
        />
        {running ? <Spinner /> : null}
      </div>

      <TerminalPane>
        {log.map((line, index) => (
          <div key={index} className="flex gap-1">
            <span className="shrink-0 text-term-dim">{line.at}</span>
            <span className={LINE_TONE[line.tone]}>{line.text}</span>
          </div>
        ))}
      </TerminalPane>

      {users.length === 0 ? null : (
        <div className="flex flex-col gap-2">
          <span className="type-body-medium">{t(`${P}.title`, { count: users.length })}</span>
          <UserRows mode={mode} users={users} />
        </div>
      )}
    </div>
  );
}
