import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  manualInputPhoneOtpMutation,
  phoneTelephonyStatusQueryKey,
  smsInboxQueryOptions,
  type SmsInboxMessage,
} from '@/entities/phone';
import { Badge, Button, CloseButton, EmptyState, Input, Modal, ModalHeader } from '@/shared/ui';

const POLL_INBOX_MS = 3000;

function formatSmsTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function SmsInboxDrawer({
  phoneId,
  onClose,
}: {
  phoneId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data } = useQuery({
    ...smsInboxQueryOptions({ query: { phone_id: phoneId } }),
    refetchInterval: POLL_INBOX_MS,
  });
  const manualType = useMutation(manualInputPhoneOtpMutation());
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [typingCode, setTypingCode] = useState<string | null>(null);
  const [customCode, setCustomCode] = useState('');

  const messages: SmsInboxMessage[] = Array.isArray(data?.messages) ? data.messages : [];

  const copyCode = (msg: SmsInboxMessage) => {
    if (!msg.code) return;
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      void navigator.clipboard.writeText(msg.code).catch(() => undefined);
    }
    setCopiedId(msg.id);
  };

  const triggerManualType = (code: string) => {
    const trimmed = code.trim();
    if (!trimmed) return;
    setTypingCode(trimmed);
    void manualType
      .mutateAsync({
        path: { phone_id: phoneId },
        body: { code: trimmed },
      })
      .finally(() => {
        setTypingCode(null);
        setCustomCode('');
        void queryClient.invalidateQueries({
          queryKey: phoneTelephonyStatusQueryKey({ path: { phone_id: phoneId } }),
        });
      })
      .catch(() => undefined);
  };

  return (
    <Modal variant="drawer-left" label={t('telephony.inbox.title')} onClose={onClose}>
      <ModalHeader title={t('telephony.inbox.title')}>
        <CloseButton aria-label={t('telephony.inbox.title')} onClick={onClose} />
      </ModalHeader>
      <div className="flex flex-1 flex-col gap-4 p-6">
        <div className="flex items-center gap-2">
          <Input
            size="sm"
            value={customCode}
            placeholder={t('telephony.inbox.manualInputPlaceholder')}
            onChange={(event) => setCustomCode(event.target.value.replace(/\D/g, '').slice(0, 8))}
          />
          <Button
            size="sm"
            variant="primary"
            disabled={customCode.length < 4}
            loading={typingCode === customCode && customCode.length >= 4}
            onClick={() => triggerManualType(customCode)}
          >
            {t('telephony.inbox.manualType')}
          </Button>
        </div>

        {messages.length === 0 ? (
          <EmptyState boxed>{t('telephony.inbox.empty')}</EmptyState>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {messages.map((msg) => (
              <li
                key={msg.id}
                className="flex flex-col gap-2 rounded-md border border-line bg-surface p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="type-body-medium">{msg.sender}</span>
                  <span className="type-small">{formatSmsTimestamp(msg.timestamp)}</span>
                </div>
                <p className="m-0 type-body break-words">{msg.text}</p>
                {msg.code ? (
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <Badge tone="info" size="sm" className="tracking-code">
                      {msg.code}
                    </Badge>
                    <Button size="sm" variant="secondary" onClick={() => copyCode(msg)}>
                      {copiedId === msg.id
                        ? t('telephony.inbox.copied')
                        : t('telephony.inbox.copyCode')}
                    </Button>
                    <Button
                      size="sm"
                      variant="primary"
                      loading={typingCode === msg.code}
                      onClick={() => triggerManualType(msg.code ?? '')}
                    >
                      {t('telephony.inbox.manualType')}
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}