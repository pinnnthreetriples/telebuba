import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  assignStandaloneProxyMutation,
  deleteStandalonePhoneMutation,
  phonesQueryKey,
  unassignStandaloneProxyMutation,
} from '@/entities/phone';
import { proxyPoolQueryOptions, ProxyPoolSelect } from '@/entities/proxy';
import type { Phone } from '@/shared/api';
import { Button, ConfirmModal } from '@/shared/ui';

// An independent emulator's own controls in its details: the pool proxy it runs on
// (a change puts the phone to sleep; the next start uses the new one) and its removal.
export function StandaloneSection({ phone, onDeleted }: { phone: Phone; onDeleted: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const assign = useMutation(assignStandaloneProxyMutation());
  const unassign = useMutation(unassignStandaloneProxyMutation());
  const remove = useMutation(deleteStandalonePhoneMutation());
  const [proxyId, setProxyId] = useState<string | null>(phone.proxy_id ?? null);
  const [confirming, setConfirming] = useState(false);
  const path = { phone_id: phone.phone_id };
  const busy = assign.isPending || unassign.isPending;

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: phonesQueryKey() });
    void queryClient.invalidateQueries({ queryKey: proxyPoolQueryOptions().queryKey });
  };
  const choose = (next: string) => {
    if (!next || next === proxyId || busy) return;
    // The global mutation cache toasts a refusal (a full proxy); the pick stays as it was.
    assign.mutate(
      { path, body: { proxy_id: next } },
      { onSuccess: () => setProxyId(next), onSettled: refresh },
    );
  };
  const detach = () => {
    unassign.mutate({ path }, { onSuccess: () => setProxyId(null), onSettled: refresh });
  };

  return (
    <div className="flex flex-col gap-2 border-t border-canvas pt-4">
      <span className="type-body-medium">{t('devices.standalone.proxyTitle')}</span>
      <span className="type-small text-content-muted">{t('devices.standalone.proxyHint')}</span>
      <ProxyPoolSelect
        value={proxyId}
        onChange={choose}
        disabled={busy}
        emptyLabel={t('devices.standalone.noFreeProxy')}
      />
      <div className="flex flex-wrap gap-2">
        {proxyId && (
          <Button size="sm" loading={unassign.isPending} disabled={busy} onClick={detach}>
            {t('devices.standalone.detachProxy')}
          </Button>
        )}
        <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>
          {t('devices.standalone.delete')}
        </Button>
      </div>
      {confirming && (
        <ConfirmModal
          title={t('devices.standalone.deleteTitle')}
          body={t('devices.standalone.deleteBody')}
          confirmLabel={t('devices.standalone.delete')}
          cancelLabel={t('devices.standalone.cancel')}
          onClose={() => setConfirming(false)}
          onConfirm={() =>
            remove.mutateAsync({ path }).then(() => {
              refresh();
              onDeleted();
            })
          }
        />
      )}
    </div>
  );
}
