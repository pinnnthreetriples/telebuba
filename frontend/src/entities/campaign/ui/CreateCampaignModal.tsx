import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

const FIELD =
  'box-border w-full rounded-[10px] border border-line-input px-3 py-[9px] text-[13px] text-ink outline-none';

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
    <Modal onClose={onClose} z={72} className="max-h-[88vh] w-[540px] overflow-y-auto">
      <div className="flex items-center gap-[11px] border-b border-[#f0eeeb] px-6 pb-[15px] pt-5">
        <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[10px] bg-primary-tint text-primary">
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
          <div className="text-[16px] font-bold text-ink">
            {t('neurocomment.modal.createCampaign.title')}
          </div>
          <div className="mt-[2px] text-[12.5px] text-ink-subtle">
            {t('neurocomment.modal.createCampaign.sub')}
          </div>
        </div>
      </div>

      <div className="px-6 pb-5 pt-[18px]">
        <div className="mb-[7px] text-[12px] font-semibold text-ink">
          {t('neurocomment.modal.createCampaign.nameLabel')}
        </div>
        <input
          value={name}
          onChange={(event) => {
            setName(event.target.value);
          }}
          placeholder={t('neurocomment.modal.createCampaign.namePlaceholder')}
          aria-label={t('neurocomment.modal.createCampaign.nameLabel')}
          className={`${FIELD} mb-4`}
        />

        <div className="mb-[7px] text-[12px] font-semibold text-ink">
          {t('neurocomment.modal.createCampaign.promptLabel')}
        </div>
        <textarea
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
          }}
          rows={4}
          placeholder={t('neurocomment.modal.createCampaign.promptPlaceholder')}
          aria-label={t('neurocomment.modal.createCampaign.promptLabel')}
          className={`${FIELD} mb-4 resize-y font-[inherit] leading-[1.5]`}
        />

        <div className="mb-[7px] text-[12px] font-semibold text-ink">
          {t('neurocomment.modal.createCampaign.channelsLabel')}
        </div>
        <div className="mb-[10px] text-[11px] text-ink-subtle">
          {t('neurocomment.modal.createCampaign.channelsHint')}
        </div>
        {channels.length > 0 ? (
          <div className="mb-3 flex flex-wrap gap-[7px]">
            {channels.map((channel, index) => (
              <span
                key={`${channel}-${String(index)}`}
                className="inline-flex items-center gap-[6px] rounded-full border border-line bg-[#f4f3f0] px-[11px] py-[5px] text-[12px] text-[#3a3a3a]"
              >
                {channel}
                <button
                  type="button"
                  aria-label={t('neurocomment.channels.remove')}
                  onClick={() => {
                    setChannels((list) => list.filter((_, i) => i !== index));
                  }}
                  className="cursor-pointer text-[14px] leading-none text-[#b5b3ae]"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <div className="flex gap-2">
          <input
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
            className={`${FIELD} flex-1`}
          />
          <button
            type="button"
            onClick={addChannel}
            className="shrink-0 rounded-[10px] bg-[#e8f0ff] px-4 py-[9px] text-[13px] font-semibold text-primary"
          >
            {t('neurocomment.modal.add')}
          </button>
        </div>
      </div>

      <div className="flex gap-2 border-t border-[#f0eeeb] px-6 pb-5 pt-[15px]">
        <button
          type="button"
          disabled={!name.trim() || !prompt.trim()}
          onClick={() => {
            onCreate({ name: name.trim(), prompt: prompt.trim(), channels });
            onClose();
          }}
          className="flex-1 rounded-full border border-primary bg-primary px-[14px] py-[10px] text-[13px] font-semibold text-white disabled:opacity-50"
        >
          {t('neurocomment.modal.createCampaign.confirm')}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="flex-1 rounded-full border border-line-input bg-white px-[14px] py-[10px] text-[13px] font-medium text-ink"
        >
          {t('neurocomment.modal.cancel')}
        </button>
      </div>
    </Modal>
  );
}
