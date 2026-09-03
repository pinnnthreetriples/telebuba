import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  adoptCampaignDiscoveryMutation,
  campaignDiscoveryQueryOptions,
  campaignsQueryOptions,
  discoveryAccountsQueryOptions,
  neurocommentBoardQueryOptions,
  startCampaignDiscoveryMutation,
} from '@/entities/campaign';
import { useLogEventStream } from '@/shared/lib';
import { Button, Modal, StatusIcon } from '@/shared/ui';

import {
  buildSearchRequest,
  canSubmit,
  EMPTY_FORM,
  resolveSelection,
  type DiscoveryFormState,
} from '../model/discovery';
import { effectiveAccountIds } from '../model/filters';
import { DiscoveryForm } from './DiscoveryForm';
import { DiscoveryResults } from './DiscoveryResults';

// The operator is watching this run, so it polls far faster than the page's 30s
// fallback net — and it stops itself once the run settles.
const SEARCH_POLL_MS = 3000;
// Accounts go busy (warming, listener, cooling) behind the operator's back and nothing
// else refreshes this list: refetchOnWindowFocus is off globally and the SSE handler
// below invalidates only the board. Polled while the form is on screen, not during a run.
const ACCOUNTS_POLL_MS = 15_000;
const CLOSE_DELAY_MS = 700;

// The adopt outcomes that get their own paragraph, in display order: "taken by another
// campaign", "comments are off there" and "a group / subscription-gated channel the row's
// traits did not yet show" are final and for different reasons (the third is not the
// operator's fault); "the link itself failed" is worth retrying, hence the danger tone.
const NOTES = [
  ['refused', 'addedRefused', 'text-warning-deep'],
  ['commentsOff', 'addedCommentsOff', 'text-warning-deep'],
  ['notAdoptable', 'addedNotAdoptable', 'text-warning-deep'],
  ['failed', 'addedFailed', 'text-danger'],
] as const;

type Props = {
  campaignId: string;
  campaignName: string;
  onClose: () => void;
};

export function ChannelDiscoveryModal({ campaignId, campaignName, onClose }: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const formId = useId();
  const [form, setForm] = useState<DiscoveryFormState>(EMPTY_FORM);
  const [submitted, setSubmitted] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [adopted, setAdopted] = useState<{
    linked: number;
    refused: number;
    commentsOff: number;
    notAdoptable: number;
    failed: number;
  } | null>(null);

  const discoveryOptions = campaignDiscoveryQueryOptions({ path: { campaign_id: campaignId } });
  const board = useQuery({
    ...discoveryOptions,
    enabled: submitted,
    // Function form (a first in this codebase): the fixed-interval constant used
    // elsewhere cannot switch itself off, and this modal must stop polling once the
    // run stops. No data means loading or errored — the query is
    // only enabled after a started search, so keep polling instead of going quiet on
    // one transient failure.
    refetchInterval: (query) =>
      query.state.data === undefined || query.state.data.progress.running === true
        ? SEARCH_POLL_MS
        : false,
  });

  // Derived, not synced in an effect: an untouched picker (null) resolves to the
  // default against whatever list is CURRENT, so an account that went busy between
  // load and click drops out of the request by itself.
  const accountsOptions = discoveryAccountsQueryOptions();
  const accounts = useQuery({
    ...accountsOptions,
    refetchInterval: submitted ? false : ACCOUNTS_POLL_MS,
  });
  const accountList = accounts.data?.items ?? [];
  const accountIds = effectiveAccountIds(form.accountIds, accountList);

  // The shared SSE stream fires for the whole app; only this one query is ours.
  useLogEventStream((entry) => {
    if (!entry.event.startsWith('neurocomment_discovery')) return;
    void queryClient.invalidateQueries({ queryKey: discoveryOptions.queryKey });
  });

  const startSearch = useMutation(startCampaignDiscoveryMutation());
  const adopt = useMutation(adoptCampaignDiscoveryMutation());

  const phase = board.data?.progress.phase ?? 'idle';
  const running = board.data?.progress.running ?? false;
  const startStatus = startSearch.data?.status;
  const refused = startStatus !== undefined && startStatus !== 'started';
  // Which account the refusal is about — by name, since the id means nothing on screen.
  const refusedId = startSearch.data?.refused_account_id;
  const refusedName =
    refusedId == null
      ? null
      : (accountList.find((account) => account.account_id === refusedId)?.name ?? refusedId);

  const runSearch = () => {
    setAdopted(null);
    startSearch.mutate(
      { path: { campaign_id: campaignId }, body: buildSearchRequest(form, accountIds) },
      {
        onSuccess: (outcome) => {
          // already_running is the one refusal with something to show: the run the
          // operator collided with is the run they wanted. Reopening the modal starts
          // over on the form, so without this there is no path back to a live board.
          if (outcome.status === 'already_running') {
            setSubmitted(true);
            return;
          }
          if (outcome.status !== 'started') return;
          setSubmitted(true);
          // reset, not invalidate: invalidate keeps the previous run's frame while it
          // refetches, and the cache would then hand this run the finished rows of the
          // last one — adoptable, and with a running:false that stops the poll.
          void queryClient.resetQueries({ queryKey: discoveryOptions.queryKey });
        },
      },
    );
  };

  const toggle = (channel: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(channel)) next.delete(channel);
      else next.add(channel);
      return next;
    });
  };

  const toggleAll = (channels: string[], nextChecked: boolean) => {
    setSelected(nextChecked ? new Set(channels) : new Set());
  };

  // Re-intersect with the LATEST eligible set: a row that flipped to comments_off
  // after the operator ticked it must drop out of the request.
  const picks = resolveSelection(selected, board.data?.candidates ?? []);

  const submitAdopt = () => {
    if (picks.length === 0) return;
    const requested = picks.length;
    // A retry after failed outcomes must not carry the previous attempt's counts: the
    // footer and its paragraphs would otherwise describe two attempts at once.
    setAdopted(null);
    // mutateAsync, not mutate+onSuccess: the callbacks live on the mutation
    // OBSERVER, which this modal unmounts with. Escape or a backdrop click while
    // the adopt was in flight linked the channels server-side and then dropped
    // all three invalidations, leaving the neurocomment board and the campaign
    // list showing a campaign without its new channels. The promise outlives the
    // component, so the cache is refreshed either way.
    void adopt
      .mutateAsync({ path: { campaign_id: campaignId }, body: { channels: picks } })
      .then((result) => {
        const outcomes = result.outcomes ?? [];
        const count = (status: string) =>
          outcomes.filter((outcome) => outcome.status === status).length;
        // "Taken by another campaign", "comments are off there" and "the link itself
        // failed" need different copy: the first two are final and for different
        // reasons, the third is worth retrying.
        const linked = count('linked');
        setAdopted({
          linked,
          refused: count('already_assigned'),
          commentsOff: count('comments_off'),
          notAdoptable: count('not_adoptable'),
          failed: count('failed'),
        });
        void queryClient.invalidateQueries({
          queryKey: neurocommentBoardQueryOptions({ path: { campaign_id: campaignId } }).queryKey,
        });
        void queryClient.invalidateQueries({ queryKey: campaignsQueryOptions().queryKey });
        void queryClient.invalidateQueries({ queryKey: discoveryOptions.queryKey });
        // Anything refused was taken between the last poll and the click. Closing on a
        // partial result would hide which picks never made it, so only a clean sweep
        // gets the green flash and the auto-close.
        if (linked === requested) setTimeout(onClose, CLOSE_DELAY_MS);
      })
      // adopt.isError renders the "add failed" paragraph while the modal is open.
      .catch(() => undefined);
  };

  // Both transitions unmount the button that had focus ("Найти" / "← Изменить
  // параметры"), which drops focus onto <body>. Modal's Tab trap is a keydown
  // handler on the dialog, so from there the next Tab walks into the page behind it.
  const contentRef = useRef<HTMLDivElement>(null);
  const opened = useRef(false);
  useEffect(() => {
    // Not on open: Modal focuses the dialog itself, and that is where its Tab trap
    // can still wrap backwards.
    if (opened.current) contentRef.current?.focus();
    opened.current = true;
  }, [submitted]);

  // Width only, no max-h/overflow-y: per Modal's contract a tall card scrolls via the
  // OVERLAY, because overflow-y on the card computes overflow-x to auto and clips the
  // HelpHint tooltips — including the only place the seed channel is documented.
  // Оболочка — как у CampaignSettingsModal: шапка, тело, подвал с кнопками.
  return (
    <Modal onClose={onClose} size="table" label={t('neurocomment.modal.discovery.title')}>
      <div className="border-b border-line-row px-2xl pb-lg pt-xl">
        <h2 className="type-dialog-title">{t('neurocomment.modal.discovery.title')}</h2>
        <p className="mt-hair type-caption">
          {t('neurocomment.modal.discovery.sub', { name: campaignName })}
        </p>
      </div>

      <div className="flex flex-col gap-2xl px-2xl py-xl">
        <div ref={contentRef} tabIndex={-1} className="outline-none">
          {submitted ? (
            <DiscoveryResults
              board={board.data}
              loading={board.isPending || (running && phase === 'searching')}
              errored={board.isError}
              selected={selected}
              onToggle={toggle}
              onToggleAll={toggleAll}
            />
          ) : (
            <DiscoveryForm
              form={form}
              formId={formId}
              accounts={accountList}
              accountsLoading={accounts.isPending}
              accountsErrored={accounts.isError}
              accountIds={accountIds}
              submitting={startSearch.isPending}
              onChange={setForm}
              onSubmit={runSearch}
            />
          )}
        </div>

        <div className="flex flex-col gap-sm empty:hidden">
          {refused ? (
            <p className="type-prose text-danger">
              {t(`neurocomment.modal.discovery.refused.${startStatus}`)}
              {refusedName === null
                ? null
                : ` — ${t('neurocomment.modal.discovery.refused.account', { name: refusedName })}`}
            </p>
          ) : null}

          {/* The request never landed, so there is no status to translate — the global
              toast fires outside the modal with a raw error code, and the form alone
              would just re-enable its button. */}
          {startSearch.isError ? (
            <p role="status" className="type-prose text-danger">
              {t('neurocomment.modal.discovery.startFailed')}
            </p>
          ) : null}

          {adopted === null
            ? null
            : NOTES.map(([field, key, tone]) =>
                adopted[field] > 0 ? (
                  <p key={key} role="status" className={`type-prose ${tone}`}>
                    {t(`neurocomment.modal.discovery.${key}`, { count: adopted[field] })}
                  </p>
                ) : null,
              )}

          {/* The request itself never landed, so nothing can be read from the outcomes —
              silence would read as "nothing happened". */}
          {adopt.isError ? (
            <p role="status" className="type-prose text-danger">
              {t('neurocomment.modal.discovery.addFailed')}
            </p>
          ) : null}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-end gap-sm border-t border-line-row px-2xl py-lg">
        {submitted ? (
          <>
            <Button
              variant="ghost"
              size="sm"
              className="mr-auto"
              onClick={() => {
                setSubmitted(false);
                // The only way back to the form, so it owns dropping the picks: the
                // next run's rows have nothing to do with the ones ticked here.
                setSelected(new Set());
                // The list was not polled during the run; the form must not reopen on it.
                void queryClient.invalidateQueries({ queryKey: accountsOptions.queryKey });
              }}
            >
              {t('neurocomment.modal.discovery.results.back')}
            </Button>
            <Button size="sm" onClick={onClose}>
              {t('neurocomment.modal.close')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              // The outcome stays set through the close delay, so a fast second click
              // cannot re-post channels that are already settled. Failed links are the
              // one outcome worth retrying, so they keep the button live.
              disabled={
                picks.length === 0 || adopt.isPending || (adopted !== null && adopted.failed === 0)
              }
              onClick={submitAdopt}
            >
              {adopted === null ? (
                t('neurocomment.modal.discovery.add', { count: picks.length })
              ) : adopted.linked === 0 ? (
                <>
                  <StatusIcon kind="err" />
                  {t('neurocomment.modal.discovery.addedNone')}
                </>
              ) : (
                <>
                  <StatusIcon kind="ok" />
                  {t('neurocomment.modal.discovery.added', { count: adopted.linked })}
                </>
              )}
            </Button>
          </>
        ) : (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setForm(EMPTY_FORM);
              }}
            >
              {t('neurocomment.modal.discovery.form.reset')}
            </Button>
            {/* Outside the <form>, reached by `form={formId}`; Enter inside the form
                still submits through the form's own handler. */}
            <Button
              type="submit"
              form={formId}
              variant="primary"
              size="sm"
              disabled={!canSubmit(form, accountIds) || startSearch.isPending}
              loading={startSearch.isPending}
            >
              {startSearch.isPending
                ? t('neurocomment.modal.discovery.form.searching')
                : t('neurocomment.modal.discovery.form.submit')}
            </Button>
          </>
        )}
      </div>
    </Modal>
  );
}
