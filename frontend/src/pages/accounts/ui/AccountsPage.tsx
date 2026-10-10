import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountDisplayName,
  accountFilterOptionsQueryOptions,
  accountFoldersQueryOptions,
  accountsQueryOptions,
  accountStatsQueryOptions,
  activeBulkMessageJobQueryOptions,
  latestBulkMessageJobQueryOptions,
  checkAccountMutation,
  deleteAccountMutation,
  invalidateAccountViews,
  openAccountWebMutation,
} from '@/entities/account';
import { meQueryOptions } from '@/shared/auth';
import {
  Button,
  EmptyState,
  Icon,
  IconButton,
  Spinner,
  type Stat,
  StatGrid,
  toastError,
} from '@/shared/ui';
import {
  ALL_VIEW,
  activeFilterKeys,
  type AccountFilters,
  DragGhost,
  FilterChips,
  FilterMenu,
  FOLDER_PANEL_ID,
  FolderDialog,
  type FolderDialogState,
  FolderEmptyState,
  FolderTabStrip,
  FolderTag,
  type FolderView,
  listQuery,
  NO_FILTERS,
  PanelFloor,
  RowPick,
  UNFILED_VIEW,
  useAccountDrag,
  useFolderActions,
} from '@/widgets/account-folders';

import type { AccountRead } from '@/shared/api';
import { useTransientFeedback } from '@/shared/lib';
import {
  AccountEdit,
  AddAccountModal,
  BulkMessageModal,
  type BulkMessageDraft,
  ProfileModal,
  ProxyAddModal,
} from '@/widgets/account-edit';
import { AccountsTable, DeleteAccountModal } from '@/widgets/accounts-table';
import { ProxyPool } from '@/widgets/proxy-pool';

import { SearchToggle } from './SearchToggle';

const PAGE_SIZE = 20;
// The generated query key embeds `query`, so an undebounced search box means a
// brand-new key per keystroke — no cached data, `isPending` true, and the table
// plus the pagination row replaced by the loading line on every character.
const SEARCH_DEBOUNCE_MS = 300;

export function AccountsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState('');
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [view, setView] = useState<FolderView>(ALL_VIEW);
  const [filters, setFilters] = useState<AccountFilters>(NO_FILTERS);
  // Ids, not rows: a refetch replaces the row objects and the selection must survive it.
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [dialog, setDialog] = useState<FolderDialogState>(null);
  // Switching tabs swaps the table for a shorter one; without a floor the document
  // shrinks under the viewport and the browser clamps the scroll. Measured at the
  // switch, before anything shrinks.
  const panelRef = useRef<HTMLDivElement>(null);
  const [panelFloor, setPanelFloor] = useState<number | undefined>(undefined);
  // A Set, not one id: check and delete are per row and both can be in flight at
  // once. With a single string the second click moved the spinner off the first
  // row and re-enabled its buttons mid-request, and the first response to land
  // cleared the OTHER row's spinner.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  // Kept apart from busyIds so opening web doesn't disable a row's check/delete
  // and its spinner sits only on the globe.
  const [openWebBusyIds, setOpenWebBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [editingRow, setEditingRow] = useState<AccountRead | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [messaging, setMessaging] = useState(false);
  const [messageDraft, setMessageDraft] = useState<BulkMessageDraft | null>(null);
  const [messageJobId, setMessageJobId] = useState<string | null>(null);
  const [messageJobOwnerId, setMessageJobOwnerId] = useState<string | null>(null);
  const [dismissedMessageJobId, setDismissedMessageJobId] = useState<string | null>(null);
  const [openingMessages, setOpeningMessages] = useState(false);
  const latestMayRestore = useRef(true);
  const me = useQuery(meQueryOptions());
  const activeMessageJob = useQuery({
    ...activeBulkMessageJobQueryOptions(),
    enabled: false,
  });
  const latestMessageJob = useQuery({
    ...latestBulkMessageJobQueryOptions(),
    enabled: Boolean(me.data?.id),
    refetchOnWindowFocus: false,
  });
  const latestMessageJobId = latestMessageJob.data?.job_id;
  useEffect(() => {
    const ownerId = me.data?.id;
    if (!ownerId) return;
    const dismissed = window.sessionStorage.getItem(`telebuba:bulk-message-dismissed:${ownerId}`);
    const stored = window.sessionStorage.getItem(`telebuba:bulk-message-job:${ownerId}`);
    setDismissedMessageJobId(dismissed);
    setMessageJobId(stored === dismissed ? null : stored);
    setMessageJobOwnerId(ownerId);
    window.sessionStorage.removeItem('telebuba:bulk-message-job-id');
  }, [me.data?.id]);
  useEffect(() => {
    if (!messageJobOwnerId || messageJobOwnerId !== me.data?.id) return;
    const key = `telebuba:bulk-message-job:${messageJobOwnerId}`;
    if (messageJobId) window.sessionStorage.setItem(key, messageJobId);
    else window.sessionStorage.removeItem(key);
  }, [messageJobId, messageJobOwnerId, me.data?.id]);
  useEffect(() => {
    if (
      latestMayRestore.current &&
      messageJobOwnerId === me.data?.id &&
      latestMessageJobId &&
      latestMessageJobId !== dismissedMessageJobId
    ) {
      setMessageJobId(latestMessageJobId);
    }
  }, [latestMessageJobId, dismissedMessageJobId, messageJobOwnerId, me.data?.id]);
  const openMessages = async () => {
    if (openingMessages) return;
    setOpeningMessages(true);
    try {
      const current = await activeMessageJob.refetch({ throwOnError: true });
      latestMayRestore.current = false;
      if (current.data?.job_id) setMessageJobId(current.data.job_id);
      else {
        // A POST may have reached the server even when its response never reached
        // this page. It may already be completed, so /active alone cannot decide
        // whether opening a fresh composer would duplicate the send.
        const latest = await latestMessageJob.refetch({ throwOnError: true });
        if (latest.data?.job_id && latest.data.job_id !== dismissedMessageJobId) {
          setMessageJobId(latest.data.job_id);
        }
      }
      setMessaging(true);
    } catch {
      toastError(t('accounts.messages.activeCheckError'));
    } finally {
      setOpeningMessages(false);
    }
  };
  const [proxyAdding, setProxyAdding] = useState(false);
  const [profilingRow, setProfilingRow] = useState<AccountRead | null>(null);

  const [debouncedSearch, setDebouncedSearch] = useState('');
  useEffect(() => {
    const id = window.setTimeout(() => {
      setDebouncedSearch(search);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(id);
    };
  }, [search]);

  const folders = useQuery(accountFoldersQueryOptions());
  const filterOptions = useQuery(accountFilterOptionsQueryOptions());
  const folderItems = folders.data?.items ?? [];
  const folderActions = useFolderActions(folderItems);
  // A folder deleted (here or in another tab) falls back to the full list.
  const activeView =
    view === ALL_VIEW ||
    view === UNFILED_VIEW ||
    !folders.data ||
    folderItems.some((folder) => folder.folder_id === view)
      ? view
      : ALL_VIEW;
  const filtered = activeFilterKeys(filters).length > 0;

  const restart = () => {
    setCursorStack([null]);
    setSelected(new Set());
  };
  const openView = (next: FolderView) => {
    const panel = panelRef.current;
    if (panel) setPanelFloor(Math.max(0, window.innerHeight - panel.getBoundingClientRect().top));
    setView(next);
    restart();
  };
  const changeFilters = (next: AccountFilters) => {
    setFilters(next);
    restart();
  };

  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;
  const { data, isPending, isError } = useQuery({
    ...accountsQueryOptions({
      query: {
        ...listQuery(activeView, filters),
        query: debouncedSearch,
        cursor,
        limit: PAGE_SIZE,
      },
    }),
    // Keep the last page on screen while the next key loads, so a search or a
    // page turn doesn't blank the table (and unmount an open edit view).
    placeholderData: keepPreviousData,
  });
  // Fleet-wide status roll-up for the tiles — spans the whole table, so the
  // counts stay correct across pagination and search (unlike counting items).
  const { data: fleetStats } = useQuery(accountStatsQueryOptions());

  // Scoped, never the whole cache — the entity owns the key set (check / delete
  // / import all touch exactly those three queries).
  const invalidate = () => {
    invalidateAccountViews(queryClient);
  };
  // Keyed by row for the same reason as `busyIds`: two checks can be in flight,
  // and a single slot would flash the second row's verdict onto the first.
  const { feedback: checkResults, mark: markChecked } = useTransientFeedback();
  const check = useMutation(checkAccountMutation());
  const remove = useMutation(deleteAccountMutation());
  const openWeb = useMutation(openAccountWebMutation());

  const markBusy = (accountId: string, busy: boolean) => {
    setBusyIds((ids) => {
      const next = new Set(ids);
      if (busy) next.add(accountId);
      else next.delete(accountId);
      return next;
    });
  };
  // mutateAsync, not mutate+onSettled: one useMutation is ONE callback slot, so
  // acting on a second row took the slot over and the first row's invalidate was
  // dropped — its refreshed status never reached the table. A promise per call
  // also captures its own accountId instead of the hook's latest variables.
  // The global mutationCache still toasts the failure; .catch only keeps the
  // rejection from escaping as unhandled.
  const runOnRow = (accountId: string, call: Promise<unknown>) => {
    markBusy(accountId, true);
    void call
      .finally(() => {
        markBusy(accountId, false);
        invalidate();
      })
      .catch(() => undefined);
  };
  // The spinner alone left the operator guessing: a check that answered
  // "unauthorized" looked exactly like one that answered "alive".
  const onCheck = (accountId: string) => {
    const call = check.mutateAsync({ body: { account_id: accountId } });
    // A second subscription to the same promise, not a wrapper: `runOnRow` owns
    // the busy flag and swallows the rejection, and threading the verdict
    // through it would put check-only state in the delete path too.
    void call.then(
      (checked) => {
        markChecked(accountId, checked.status === 'alive');
      },
      () => {
        markChecked(accountId, false);
      },
    );
    runOnRow(accountId, call);
  };
  const onDelete = (accountId: string) => {
    setDeletingId(accountId);
  };
  // Not runOnRow: opening web changes no account data, so there is nothing to
  // invalidate, and its busy flag lives in its own set. Refusals (no proxy, no
  // browser, relay or launch failure) surface through the global MutationCache
  // toast; the .catch only keeps the rejection from escaping as unhandled. A
  // window that opened without completing the login is NOT a refusal — that is
  // the signed_in toast below.
  const onOpenWeb = (accountId: string) => {
    setOpenWebBusyIds((ids) => new Set(ids).add(accountId));
    void openWeb
      .mutateAsync({ path: { account_id: accountId } })
      .then((result) => {
        // A 200 is not a login: every QR token can be refused for 90s, or the 2FA
        // screen can be left standing, and the window still opens. Ignoring the body
        // showed that as a silent success while the browser sat on a login screen.
        if (!result.signed_in) toastError(t('accounts.openWeb.notSignedIn'));
      })
      .catch(() => undefined)
      .finally(() => {
        setOpenWebBusyIds((ids) => {
          const next = new Set(ids);
          next.delete(accountId);
          return next;
        });
      });
  };
  const confirmDelete = () => {
    if (!deletingId) return;
    runOnRow(deletingId, remove.mutateAsync({ path: { account_id: deletingId } }));
  };
  const items = data?.items ?? [];
  const { drag, startDrag } = useAccountDrag((folderId, accountIds) => {
    void folderActions.addAccounts(folderId, accountIds).catch(() => undefined);
  });
  const folderName = (folderId: string) =>
    folderItems.find((folder) => folder.folder_id === folderId)?.name ?? '';
  const pageIds = items.map((account) => account.account_id);
  const selectedOnPage = pageIds.filter((id) => selected.has(id)).length;
  const allSelected = pageIds.length > 0 && selectedOnPage === pageIds.length;
  const toggleSelected = (accountId: string) => {
    setSelected((ids) => {
      const next = new Set(ids);
      if (next.has(accountId)) next.delete(accountId);
      else next.add(accountId);
      return next;
    });
  };
  // The design's five stat tiles (accStats): total / active / idle / needs-code /
  // problem, each with its own colour. Values come from the fleet-wide stats
  // query, not the current page, so they hold across pagination and search.
  const stats: Stat[] = [
    {
      label: t('accounts.stats.total'),
      value: fleetStats?.total ?? 0,
      tone: 'default',
    },
    { label: t('accounts.stats.active'), value: fleetStats?.active ?? 0, tone: 'success' },
    { label: t('accounts.stats.idle'), value: fleetStats?.idle ?? 0, tone: 'warning' },
    {
      label: t('accounts.stats.code'),
      value: fleetStats?.needs_code ?? 0,
      tone: 'primary',
    },
    { label: t('accounts.stats.problem'), value: fleetStats?.problem ?? 0, tone: 'danger' },
  ];

  const messagesBusy =
    me.isPending ||
    (me.isSuccess && (latestMessageJob.isFetching || messageJobOwnerId !== me.data.id)) ||
    openingMessages;

  const hasPrev = cursorStack.length > 1;
  const hasNext = Boolean(data?.next_cursor);

  // Derive the edited/profiled row from the live list each render so it
  // reflects the latest refetch (e.g. status flips from 'unauthorized' after a
  // code login), rather than a stale snapshot captured at click time. Both keep
  // the click-time row as a fallback so an open view doesn't vanish when the
  // account drops out of the current filtered page after an invalidate (e.g. a
  // renamed account no longer matches the search). The edit view needs that at
  // least as much as the modal: its 2FA card holds a one-time plaintext password
  // and that card's own success invalidates this list.
  const editing = editingRow
    ? (items.find((a) => a.account_id === editingRow.account_id) ?? editingRow)
    : null;
  const profiling = profilingRow
    ? (items.find((a) => a.account_id === profilingRow.account_id) ?? profilingRow)
    : null;
  if (editing) {
    return (
      <AccountEdit
        account={editing}
        onBack={() => {
          setEditingRow(null);
        }}
      />
    );
  }

  return (
    <div className="tb-fadeup">
      {/* Зазор ставит страница, а не карточка пула. Обёрткой, а не `gap` на колонке:
          у этой страницы ритм из двух шагов — `lg` между блоками и `xl` под заголовком,
          что видно и на других страницах, — а `gap` умеет выразить только один. Замена
          обоих на один `lg` подровняла бы страницу, разойдясь с двумя соседними. */}
      <div className="mb-4">
        <ProxyPool
          onAdd={() => {
            setProxyAdding(true);
          }}
        />
      </div>

      <div className="mb-6">
        <h1 className="m-0 type-h1">{t('accounts.title')}</h1>
      </div>

      <StatGrid stats={stats} className="mb-4" />

      <div
        ref={panelRef}
        // A measured floor, not a design value: the height the panel had on screen at
        // the moment of the tab switch.
        style={panelFloor === undefined ? undefined : { minHeight: panelFloor }}
      >
        <FolderTabStrip
          view={activeView}
          onView={openView}
          folders={folders.data}
          selectAll={{
            checked: allSelected,
            indeterminate: selectedOnPage > 0 && !allSelected,
            disabled: pageIds.length === 0,
            onToggle: () => {
              setSelected(allSelected ? new Set() : new Set(pageIds));
            },
          }}
          dropFolderId={drag?.folderId ?? null}
          onCreate={() => {
            setDialog({ kind: 'create' });
          }}
          onSettings={(folderId) => {
            setDialog({ kind: 'settings', folderId });
          }}
          actions={
            <>
              <SearchToggle
                value={search}
                onChange={(value) => {
                  setSearch(value);
                  restart();
                }}
              />
              <FilterMenu
                filters={filters}
                onChange={changeFilters}
                options={filterOptions.data}
                found={data?.total}
              />
              <IconButton
                size="md"
                tone="neutral"
                disabled={messagesBusy}
                aria-label={t('accounts.messages.open')}
                title={t('accounts.messages.open')}
                onClick={() => {
                  void openMessages();
                }}
              >
                {messagesBusy ? <Spinner tone="default" /> : <Icon name="send" size={16} />}
              </IconButton>
              <IconButton
                size="md"
                tone="primary"
                aria-label={t('accounts.actions.add')}
                title={t('accounts.actions.add')}
                onClick={() => {
                  setAdding(true);
                }}
              >
                <Icon name="user-plus" size={16} />
              </IconButton>
            </>
          }
        />
        <FilterChips filters={filters} onChange={changeFilters} options={filterOptions.data} />
        <div id={FOLDER_PANEL_ID} role="tabpanel">
          {isPending ? (
            <PanelFloor>
              <p className="text-content-muted">{t('accounts.loading')}</p>
            </PanelFloor>
          ) : isError ? (
            <PanelFloor>
              <p role="alert" className="text-danger">
                {t('accounts.error')}
              </p>
            </PanelFloor>
          ) : items.length > 0 ? (
            <AccountsTable
              data={items}
              joined
              selectedIds={selected}
              renderPick={(account) => (
                <RowPick
                  name={accountDisplayName(account)}
                  selected={selected.has(account.account_id)}
                  onToggle={() => {
                    toggleSelected(account.account_id);
                  }}
                  onGripDown={(event) => {
                    startDrag(event, account.account_id, selected);
                  }}
                />
              )}
              renderTags={(account) => (
                <FolderTag
                  folderIds={account.folder_ids ?? []}
                  folders={folderItems}
                  onOpenFolder={openView}
                />
              )}
              onCheck={onCheck}
              onDelete={onDelete}
              onOpen={(account) => {
                setEditingRow(account);
              }}
              onProfile={(account) => {
                setProfilingRow(account);
              }}
              onOpenWeb={onOpenWeb}
              busyIds={busyIds}
              openWebBusyIds={openWebBusyIds}
              checkResults={checkResults}
            />
          ) : activeView === ALL_VIEW && !filtered && debouncedSearch === '' ? (
            <PanelFloor>
              <EmptyState size="xl">{t('accounts.empty')}</EmptyState>
            </PanelFloor>
          ) : (
            <FolderEmptyState
              onResetFilters={
                filtered
                  ? () => {
                      changeFilters(NO_FILTERS);
                    }
                  : null
              }
            />
          )}
        </div>
      </div>
      {/* The pagination row lives outside the empty branch: deleting the last row of
          page 2 empties the list, and with Prev buried in the else-branch the only ways
          back were the search box and a reload. */}
      {!isPending && !isError && (items.length > 0 || hasPrev) ? (
        <div className="mt-4 flex items-center justify-end gap-2">
          <Button
            size="sm"
            disabled={!hasPrev}
            onClick={() => {
              setCursorStack((stack) => stack.slice(0, -1));
              setSelected(new Set());
            }}
          >
            {t('accounts.pagination.prev')}
          </Button>
          <Button
            size="sm"
            disabled={!hasNext}
            onClick={() => {
              setCursorStack((stack) => [...stack, data?.next_cursor ?? null]);
              setSelected(new Set());
            }}
          >
            {t('accounts.pagination.next')}
          </Button>
        </div>
      ) : null}
      {drag ? (
        <DragGhost
          drag={drag}
          accounts={items}
          folderName={drag.folderId ? folderName(drag.folderId) : null}
        />
      ) : null}
      <FolderDialog
        state={dialog}
        onChange={setDialog}
        folders={folderItems}
        actions={folderActions}
      />
      {deletingId ? (
        <DeleteAccountModal
          phone={items.find((a) => a.account_id === deletingId)?.phone ?? deletingId}
          onClose={() => {
            setDeletingId(null);
          }}
          onConfirm={confirmDelete}
        />
      ) : null}
      {adding ? (
        <AddAccountModal
          onClose={() => {
            setAdding(false);
          }}
          onImported={invalidate}
        />
      ) : null}
      {messaging ? (
        <BulkMessageModal
          jobId={messageJobId}
          initialDraft={messageDraft}
          onJobStarted={(id) => {
            setMessageJobId(id);
            setMessageDraft(null);
            setDismissedMessageJobId(null);
            if (messageJobOwnerId) {
              window.sessionStorage.removeItem(
                `telebuba:bulk-message-dismissed:${messageJobOwnerId}`,
              );
            }
          }}
          onNewJob={() => {
            if (messageJobId && messageJobOwnerId) {
              setDismissedMessageJobId(messageJobId);
              window.sessionStorage.setItem(
                `telebuba:bulk-message-dismissed:${messageJobOwnerId}`,
                messageJobId,
              );
            }
            setMessageJobId(null);
          }}
          onDraftSaved={setMessageDraft}
          onClose={() => {
            setMessaging(false);
          }}
        />
      ) : null}
      {proxyAdding ? (
        <ProxyAddModal
          onClose={() => {
            setProxyAdding(false);
          }}
        />
      ) : null}
      {profiling ? (
        <ProfileModal
          account={profiling}
          onClose={() => {
            setProfilingRow(null);
          }}
        />
      ) : null}
    </div>
  );
}
