import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/shared/lib/cn';
import { Button, ConfirmModal, Icon, IconButton, Input } from '@/shared/ui';

import { usersToCsv, type ParsedBase } from '../model/userParser';
import { UserRows } from './UserParserResults';

const P = 'userParser.bases';

function download(name: string, body: string, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

// Вкладка «Базы»: каждый сбор — папка. Слева папки, справа люди выбранной папки.
export function UserParserBases({
  bases,
  onRename,
  onDelete,
}: {
  bases: ParsedBase[];
  onRename: (id: string, name: string) => void;
  onDelete: (id: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const searchId = useId();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [renaming, setRenaming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const date = (iso: string) => new Date(iso).toLocaleDateString(i18n.language);

  if (bases.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-center">
        <Icon name="file" size={20} />
        <span className="type-body-medium">{t(`${P}.emptyTitle`)}</span>
        <span className="type-small">{t(`${P}.emptyHint`)}</span>
      </div>
    );
  }

  const base = bases.find((b) => b.id === selectedId) ?? bases[0];
  if (base === undefined) return null;
  const needle = search.trim().toLowerCase();
  const shown = needle
    ? base.users.filter((user) =>
        `${user.name} ${user.username ?? ''} ${String(user.id)}`.toLowerCase().includes(needle),
      )
    : base.users;
  const file = base.name.replace(/[^\p{L}\p{N}]+/gu, '_');

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:gap-6">
      {/* Папки: название, режим, сколько людей, когда. */}
      <ul className="flex flex-col gap-1 sm:w-tip sm:shrink-0">
        {bases.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              aria-current={item.id === base.id}
              onClick={() => {
                setSelectedId(item.id);
                setSearch('');
                setRenaming(null);
              }}
              className={cn(
                'flex w-full items-start gap-2 rounded-md px-3 py-2 text-left transition-colors',
                item.id === base.id ? 'bg-canvas' : 'hover:bg-canvas',
              )}
            >
              <Icon name="file" size={16} />
              <span className="min-w-0 flex-1">
                <span className="block break-words type-body-medium">{item.name}</span>
                <span className="block truncate type-small">
                  {t(`${P}.folderMeta`, {
                    mode: t(`userParser.bases.modeName.${item.mode}`),
                    count: item.users.length,
                  })}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>

      {/* Содержимое папки. */}
      <section className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {renaming === base.id ? (
            <form
              className="flex min-w-0 flex-1 items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                const name = new FormData(event.currentTarget).get('name');
                if (typeof name === 'string' && name.trim() !== '') onRename(base.id, name.trim());
                setRenaming(null);
              }}
            >
              <Input
                name="name"
                size="sm"
                autoFocus
                defaultValue={base.name}
                aria-label={t(`${P}.rename`)}
              />
              <Button type="submit" size="sm">
                {t(`${P}.save`)}
              </Button>
            </form>
          ) : (
            <div className="flex min-w-0 flex-1 items-center gap-1">
              <h3 className="truncate type-h3">{base.name}</h3>
              <IconButton
                size="sm"
                shape="circle"
                aria-label={t(`${P}.rename`)}
                onClick={() => {
                  setRenaming(base.id);
                }}
              >
                <Icon name="pencil" size={14} />
              </IconButton>
            </div>
          )}
          <Button
            size="sm"
            className="gap-1"
            onClick={() => {
              download(`${file}.csv`, usersToCsv(base.users), 'text/csv');
            }}
          >
            <Icon name="download" size={14} />
            CSV
          </Button>
          <Button
            size="sm"
            className="gap-1"
            onClick={() => {
              download(`${file}.json`, JSON.stringify(base.users, null, 2), 'application/json');
            }}
          >
            <Icon name="download" size={14} />
            JSON
          </Button>
          <IconButton
            size="sm"
            tone="danger"
            aria-label={t(`${P}.delete`)}
            onClick={() => {
              setDeleting(true);
            }}
          >
            <Icon name="trash" size={14} />
          </IconButton>
        </div>

        <p className="type-small">
          {t(`${P}.summary`, {
            count: base.users.length,
            sources: base.sources.length,
            date: date(base.createdAt),
          })}
        </p>

        <Input
          id={searchId}
          size="sm"
          value={search}
          placeholder={t(`${P}.search`)}
          aria-label={t(`${P}.search`)}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
        />

        {shown.length === 0 ? (
          <p className="py-8 text-center type-small">{t(`${P}.nothingFound`)}</p>
        ) : (
          <UserRows mode={base.mode} users={shown} />
        )}
      </section>

      {deleting ? (
        <ConfirmModal
          title={t(`${P}.deleteTitle`, { name: base.name })}
          body={t(`${P}.deleteBody`, { count: base.users.length })}
          confirmLabel={t(`${P}.deleteConfirm`)}
          cancelLabel={t(`${P}.cancel`)}
          onClose={() => {
            setDeleting(false);
          }}
          onConfirm={() => {
            onDelete(base.id);
            setSelectedId(null);
          }}
        />
      ) : null}
    </div>
  );
}
