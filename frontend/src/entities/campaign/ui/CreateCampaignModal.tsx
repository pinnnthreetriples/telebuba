import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Input, Modal, Textarea } from '@/shared/ui';

// Design modal: create-campaign (L1424-1458) — name + LLM prompt + a list of
// campaign channels added as chips.
export function CreateCampaignModal({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (input: { name: string; prompt: string; channels: string[] }) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [channels, setChannels] = useState<string[]>([]);
  const [channelInput, setChannelInput] = useState('');

  const addChannel = () => {
    const value = channelInput.trim();
    if (!value) return;
    setChannels((list) => [...list, value]);
    setChannelInput('');
  };

  return (
    <Modal
      onClose={onClose}
      className="w-[540px]"
      label={t('neurocomment.modal.createCampaign.title')}
    >
      <div className="flex items-center gap-md border-b border-line-row px-6 pb-[15px] pt-5">
        <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary">
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M12 5v14M5 12h14" />
          </svg>
        </span>
        <div>
          <div className="text-title font-bold text-ink">
            {t('neurocomment.modal.createCampaign.title')}
          </div>
          <div className="mt-[2px] text-body text-ink-subtle">
            {t('neurocomment.modal.createCampaign.sub')}
          </div>
        </div>
      </div>

      <div className="px-6 pb-5 pt-[18px]">
        <div className="mb-[7px] text-body font-semibold text-ink">
          {t('neurocomment.modal.createCampaign.nameLabel')}
        </div>
        <Input
          className="mb-4"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
          }}
          placeholder={t('neurocomment.modal.createCampaign.namePlaceholder')}
          aria-label={t('neurocomment.modal.createCampaign.nameLabel')}
        />

        <div className="mb-[7px] text-body font-semibold text-ink">
          {t('neurocomment.modal.createCampaign.promptLabel')}
        </div>
        <Textarea
          className="mb-4 resize-y font-[inherit] leading-[1.5]"
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
          }}
          rows={4}
          placeholder={t('neurocomment.modal.createCampaign.promptPlaceholder')}
          aria-label={t('neurocomment.modal.createCampaign.promptLabel')}
        />

        <div className="mb-[7px] text-body font-semibold text-ink">
          {t('neurocomment.modal.createCampaign.channelsLabel')}
        </div>
        <div className="mb-[10px] text-tiny text-ink-subtle">
          {t('neurocomment.modal.createCampaign.channelsHint')}
        </div>
        {channels.length > 0 ? (
          <div className="mb-3 flex flex-wrap gap-sm">
            {channels.map((channel, index) => (
              <span
                key={`${channel}-${String(index)}`}
                className="inline-flex items-center gap-sm rounded-full border border-line bg-track px-[11px] py-[5px] text-body text-ink-body"
              >
                {channel}
                <button
                  type="button"
                  aria-label={t('neurocomment.channels.remove')}
                  onClick={() => {
                    setChannels((list) => list.filter((_, i) => i !== index));
                  }}
                  className="cursor-pointer text-lead leading-none text-ink-subtle"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <div className="flex gap-sm">
          <Input
            className="flex-1"
            value={channelInput}
            onChange={(event) => {
              setChannelInput(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                addChannel();
              }
            }}
            placeholder={t('neurocomment.channels.placeholder')}
            aria-label={t('neurocomment.channels.placeholder')}
          />
          <Button
            variant="ghost"
            size="sm"
            className="rounded-lg bg-primary-tint text-primary"
            onClick={addChannel}
          >
            {t('neurocomment.modal.add')}
          </Button>
        </div>
      </div>

      <div className="flex gap-sm border-t border-line-row px-6 pb-5 pt-[15px]">
        <button
          type="button"
          disabled={!name.trim() || !prompt.trim()}
          onClick={() => {
            onCreate({ name: name.trim(), prompt: prompt.trim(), channels });
            onClose();
          }}
          className="flex-1 rounded-full border border-primary bg-primary px-[14px] py-[10px] text-lead font-semibold text-white disabled:opacity-50"
        >
          {t('neurocomment.modal.createCampaign.confirm')}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="flex-1 rounded-full border border-line-input bg-white px-[14px] py-[10px] text-lead font-semibold text-ink"
        >
          {t('neurocomment.modal.cancel')}
        </button>
      </div>
    </Modal>
  );
}
