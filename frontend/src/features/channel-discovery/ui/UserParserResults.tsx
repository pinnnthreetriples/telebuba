import { useTranslation } from 'react-i18next';

import type { UserParserUser } from '@/shared/api';
import { Badge, Button, Spinner, StatGrid, TerminalPane } from '@/shared/ui';

import type { ParserMode } from '../model/userParser';
import type { ParserLogLine } from '../model/userParserLog';

const LINE_TONE = {
  text: 'text-term-text',
  success: 'text-term-success',
  warning: 'text-term-warning',
} as const;

const P = 'userParser.results';

function displayName(user: UserParserUser): string {
  return [user.first_name, user.last_name].filter(Boolean).join(' ') || `#${String(user.user_id)}`;
}

// Один пользователь — одна строка. Общая для итогов прогона и вкладки «Базы».
// Строки, а не DataTable: в окне уже 880px таблица уходит в карточки, и 60 карточек по
// шесть полей растягивают окно на десятки экранов.
export function UserRows({
  mode,
  users,
  hasMore = false,
  loadingMore = false,
  onMore,
}: {
  mode: ParserMode;
  users: UserParserUser[];
  hasMore?: boolean;
  loadingMore?: boolean;
  onMore?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const date = (iso: string | null | undefined) =>
    iso ? new Date(iso).toLocaleDateString(i18n.language) : '—';
  return (
    <ul className="tb-scroll max-h-dialog overflow-y-auto rounded-lg border border-line">
      {users.map((user) => {
        const sources = user.sources ?? [];
        return (
          <li
            key={user.user_id}
            className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-canvas px-4 py-2 first:border-t-0"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1 type-body-medium">
                <span className="truncate">{displayName(user)}</span>
                {user.is_premium ? (
                  <Badge tone="warning" size="sm">
                    Premium
                  </Badge>
                ) : null}
              </div>
              <div className="truncate type-small">
                {user.username ? `@${user.username}` : t(`${P}.noUsername`)} · {user.user_id}
              </div>
            </div>
            {mode === 'members' ? null : (
              <span className="type-small">
                {t(`${P}.count.${mode}`, { count: user.message_count ?? 0 })} ·{' '}
                {date(user.first_at)} — {date(user.last_at)}
              </span>
            )}
            <span className="type-small">{t(`${P}.lastSeen.${user.last_seen ?? 'hidden'}`)}</span>
            <span className="max-w-name truncate type-small" title={sources.join(', ')}>
              {sources.length > 1 ? t(`${P}.inSources`, { count: sources.length }) : sources[0]}
            </span>
          </li>
        );
      })}
      {hasMore && onMore !== undefined ? (
        <li className="flex justify-center border-t border-canvas px-4 py-2">
          <Button variant="ghost" size="sm" disabled={loadingMore} onClick={onMore}>
            {t(`${P}.more`)}
          </Button>
        </li>
      ) : null}
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
  kept: number;
  accountCount: number;
  log: ParserLogLine[];
  users: UserParserUser[];
  hasMore: boolean;
  loadingMore: boolean;
  onMore: () => void;
};

export function UserParserResults({
  mode,
  running,
  done,
  total,
  raw,
  kept,
  accountCount,
  log,
  users,
  hasMore,
  loadingMore,
  onMore,
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
            { label: t(`${P}.users`), value: kept, tone: 'success' },
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
          <span className="type-body-medium">{t(`${P}.title`, { count: kept })}</span>
          <UserRows
            mode={mode}
            users={users}
            hasMore={hasMore}
            loadingMore={loadingMore}
            onMore={onMore}
          />
        </div>
      )}
    </div>
  );
}
