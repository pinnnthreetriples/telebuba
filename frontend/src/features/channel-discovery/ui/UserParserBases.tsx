import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  deleteUserParserBaseMutation,
  renameUserParserBaseMutation,
  userParserBasesQueryOptions,
} from '@/entities/user-parser';
import { cn } from '@/shared/lib/cn';
import { Button, ConfirmModal, Icon, IconButton, Input } from '@/shared/ui';

import { useUserPages } from '../model/useUserPages';
import { downloadExport } from '../model/userParserExport';
import { UserRows } from './UserParserResults';

const P = 'userParser.bases';
const SEARCH_DEBOUNCE_MS = 300;

// Вкладка «Базы»: каждый сбор — папка. Слева папки, справа люди выбранной папки.
// Папки, люди, поиск, переименование и удаление — на сервере; удаляет только оператор.
export function UserParserBases() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const searchId = useId();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [needle, setNeedle] = useState('');
  const [renaming, setRenaming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const date = (iso: string) => new Date(iso).toLocaleDateString(i18n.language);

  const basesOptions = userParserBasesQueryOptions();
  const bases = useQuery(basesOptions).data?.items ?? [];
  const base = bases.find((b) => b.run_id === selectedId) ?? bases[0];
  const people = useUserPages(base?.run_id ?? null, needle);
  const refreshBases = () => queryClient.invalidateQueries({ queryKey: basesOptions.queryKey });
  const rename = useMutation({ ...renameUserParserBaseMutation(), onSuccess: refreshBases });
  const remove = useMutation({ ...deleteUserParserBaseMutation(), onSuccess: refreshBases });

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setNeedle(search.trim());
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [search]);

  if (base === undefined) {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-center">
        <Icon name="file" size={20} />
        <span className="type-body-medium">{t(`${P}.emptyTitle`)}</span>
        <span className="type-small">{t(`${P}.emptyHint`)}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:gap-6">
      {/* Папки: название, режим, сколько людей, когда. */}
      <ul className="flex flex-col gap-1 sm:w-tip sm:shrink-0">
        {bases.map((item) => (
          <li key={item.run_id}>
            <button
              type="button"
              aria-current={item.run_id === base.run_id}
              onClick={() => {
                setSelectedId(item.run_id);
                setSearch('');
                setNeedle('');
                setRenaming(null);
              }}
              className={cn(
                'flex w-full items-start gap-2 rounded-md px-3 py-2 text-left transition-colors',
                item.run_id === base.run_id ? 'bg-canvas' : 'hover:bg-canvas',
              )}
            >
              <Icon name="file" size={16} />
              <span className="min-w-0 flex-1">
                <span className="block break-words type-body-medium">{item.name}</span>
                <span className="block truncate type-small">
                  {t(`${P}.folderMeta`, {
                    mode: t(`userParser.bases.modeName.${item.mode}`),
                    count: item.kept,
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
          {renaming === base.run_id ? (
            <form
              className="flex min-w-0 flex-1 items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                const name = new FormData(event.currentTarget).get('name');
                if (typeof name === 'string' && name.trim() !== '') {
                  rename.mutate({ path: { run_id: base.run_id }, body: { name: name.trim() } });
                }
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
                  setRenaming(base.run_id);
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
              downloadExport(base.run_id, 'csv');
            }}
          >
            <Icon name="download" size={14} />
            CSV
          </Button>
          <Button
            size="sm"
            className="gap-1"
            onClick={() => {
              downloadExport(base.run_id, 'json');
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
            count: base.kept,
            sources: base.sources.length,
            date: date(base.created_at),
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

        {people.users.length === 0 ? (
          people.loading ? null : (
            <p className="py-8 text-center type-small">{t(`${P}.nothingFound`)}</p>
          )
        ) : (
          <UserRows
            mode={base.mode}
            users={people.users}
            hasMore={people.hasMore}
            loadingMore={people.loadingMore}
            onMore={people.more}
          />
        )}
      </section>

      {deleting ? (
        <ConfirmModal
          title={t(`${P}.deleteTitle`, { name: base.name })}
          body={t(`${P}.deleteBody`, { count: base.kept })}
          confirmLabel={t(`${P}.deleteConfirm`)}
          cancelLabel={t(`${P}.cancel`)}
          onClose={() => {
            setDeleting(false);
          }}
          onConfirm={() => {
            remove.mutate({ path: { run_id: base.run_id } });
            setSelectedId(null);
          }}
        />
      ) : null}
    </div>
  );
}
