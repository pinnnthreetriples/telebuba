import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  adoptCampaignDiscoveryMutation,
  campaignDiscoveryQueryOptions,
  campaignsQueryOptions,
  neurocommentBoardQueryOptions,
  startCampaignDiscoveryMutation,
} from '@/entities/campaign';
import { warmingSettingsQueryOptions } from '@/entities/warming';
import { useLogEventStream } from '@/shared/lib';
import { Modal, StatusIcon } from '@/shared/ui';

import {
  buildSearchRequest,
  EMPTY_FORM,
  resolveSelection,
  type DiscoveryFormState,
} from '../model/discovery';
import { DiscoveryForm } from './DiscoveryForm';
import { DiscoveryResults } from './DiscoveryResults';

// The operator is watching this run, so it polls far faster than the page's 30s
// fallback net — and it stops itself once the run settles.
const SEARCH_POLL_MS = 3000;
const CLOSE_DELAY_MS = 700;

type Props = {
  campaignId: string;
  campaignName: string;
  onClose: () => void;
};

export function ChannelDiscoveryModal({ campaignId, campaignName, onClose }: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<DiscoveryFormState>(EMPTY_FORM);
  const [submitted, setSubmitted] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [addedCount, setAddedCount] = useState<number | null>(null);

  // Whether the external catalogue is usable is server state; the modal owns its
  // own I/O, so it reads the settings row rather than taking it as a prop.
  const settings = useQuery(warmingSettingsQueryOptions());
  const telemetrConfigured = settings.data?.has_telemetr_key ?? false;

  const discoveryOptions = campaignDiscoveryQueryOptions({ path: { campaign_id: campaignId } });
  const board = useQuery({
    ...discoveryOptions,
    enabled: submitted,
    // Function form (a first in this codebase): the fixed-interval constant used
    // elsewhere cannot switch itself off, and this modal must stop polling once the
    // run reaches a terminal phase. No data means loading or errored — the query is
    // only enabled after a started search, so keep polling instead of going quiet on
    // one transient failure.
    refetchInterval: (query) =>
      query.state.data === undefined || query.state.data.progress.running === true
        ? SEARCH_POLL_MS
        : false,
  });

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

  const runSearch = () => {
    setAddedCount(null);
    startSearch.mutate(
      { path: { campaign_id: campaignId }, body: buildSearchRequest(form) },
      {
        onSuccess: (outcome) => {
          if (outcome.status !== 'started') return;
          setSubmitted(true);
          void queryClient.invalidateQueries({ queryKey: discoveryOptions.queryKey });
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
    adopt.mutate(
      { path: { campaign_id: campaignId }, body: { channels: picks } },
      {
        onSuccess: (result) => {
          const linked = (result.outcomes ?? []).filter(
            (outcome) => outcome.status === 'linked',
          ).length;
          setAddedCount(linked);
          void queryClient.invalidateQueries({
            queryKey: neurocommentBoardQueryOptions({ path: { campaign_id: campaignId } }).queryKey,
          });
          void queryClient.invalidateQueries({ queryKey: campaignsQueryOptions().queryKey });
          void queryClient.invalidateQueries({ queryKey: discoveryOptions.queryKey });
          // Every pick was taken between the last poll and the click: stay open so the
          // refreshed rows explain the no-op instead of flashing a green "done".
          if (linked > 0) setTimeout(onClose, CLOSE_DELAY_MS);
        },
      },
    );
  };

  return (
    <Modal onClose={onClose} z={72} className="w-[920px] max-h-[88vh] overflow-y-auto">
      <div className="p-[18px]">
        <h2 className="text-[15px] font-semibold">{t('neurocomment.modal.discovery.title')}</h2>
        <p className="mt-[3px] text-[12px] text-ink-subtle">
          {t('neurocomment.modal.discovery.sub', { name: campaignName })}
        </p>

        <div className="mt-[15px]">
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
              telemetrConfigured={telemetrConfigured}
              submitting={startSearch.isPending}
              onChange={setForm}
              onSubmit={runSearch}
            />
          )}
        </div>

        {refused ? (
          <p className="mt-[11px] text-[12px] text-danger">
            {t(`neurocomment.modal.discovery.refused.${startStatus}`)}
          </p>
        ) : null}

        {submitted ? (
          <div className="mt-[15px] flex items-center justify-between gap-2 border-t border-line pt-[13px]">
            <button
              type="button"
              onClick={() => {
                setSubmitted(false);
                // The only way back to the form, so it owns dropping the picks: the
                // next run's rows have nothing to do with the ones ticked here.
                setSelected(new Set());
              }}
              className="text-[12.5px] text-ink-muted hover:text-primary"
            >
              {t('neurocomment.modal.discovery.results.back')}
            </button>
            <div className="flex items-center gap-[9px]">
              <button
                type="button"
                onClick={onClose}
                className="rounded-[10px] px-[13px] py-[8px] text-[12.5px] text-ink-muted hover:text-primary"
              >
                {t('neurocomment.modal.close')}
              </button>
              <button
                type="button"
                // addedCount stays set through the close delay, so a fast second click
                // cannot re-post the same channels.
                disabled={picks.length === 0 || adopt.isPending || addedCount !== null}
                onClick={submitAdopt}
                className="inline-flex items-center gap-[6px] rounded-[10px] bg-primary px-[15px] py-[8px] text-[12.5px] font-medium text-white disabled:opacity-50"
              >
                {addedCount === null ? (
                  t('neurocomment.modal.discovery.add', { count: picks.length })
                ) : addedCount === 0 ? (
                  <>
                    <StatusIcon kind="err" />
                    {t('neurocomment.modal.discovery.addedNone')}
                  </>
                ) : (
                  <>
                    <StatusIcon kind="ok" />
                    {t('neurocomment.modal.discovery.added', { count: addedCount })}
                  </>
                )}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
