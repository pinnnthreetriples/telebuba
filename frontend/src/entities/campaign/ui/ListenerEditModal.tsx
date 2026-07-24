import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

const TRIGGER =
  'tb-time flex w-full cursor-pointer items-center justify-between rounded-[10px] border border-line-input bg-white px-[13px] py-[10px] text-[13px]';

// Design modal: listener-edit (L1387-1422) — pick the listener account from a
// custom dropdown, save with a check→"Сохранено" swap.
export function ListenerEditModal({
  options,
  selected,
  onClose,
  onSave,
}: {
  options: { id: string; name: string }[];
  selected: string | null;
  onClose: () => void;
  onSave: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [pick, setPick] = useState(selected);
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const current = options.find((o) => o.id === pick);

  const save = () => {
    if (pick) onSave(pick);
    setSaved(true);
    setTimeout(onClose, 650);
  };

  return (
    <Modal onClose={onClose} z={72} className="w-[440px]">
      <div className="p-6">
        <div className="mb-[6px] flex items-center gap-[11px]">
          <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[10px] bg-primary-tint text-primary">
            <svg
              width="17"
              height="17"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M2 10v3" />
              <path d="M6 6v11" />
              <path d="M10 3v18" />
              <path d="M14 8v7" />
              <path d="M18 5v13" />
              <path d="M22 10v3" />
            </svg>
          </span>
          <div className="flex-1">
            <div className="text-[16px] font-bold">{t('neurocomment.listener.title')}</div>
            <div className="mt-px text-[12.5px] text-ink-subtle">
              {t('neurocomment.modal.listenerEdit.sub')}
            </div>
          </div>
          <button
            type="button"
            aria-label={t('neurocomment.modal.close')}
            onClick={onClose}
            className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full border border-line bg-white text-[16px] text-ink-muted"
          >
            ×
          </button>
        </div>

        <div className="mb-[7px] mt-[18px] text-[12px] font-medium text-[#3a3a3a]">
          {t('neurocomment.modal.listenerEdit.account')}
        </div>
        <div className="relative">
          <div
            role="button"
            tabIndex={0}
            onClick={() => {
              setOpen((v) => !v);
            }}
            className={TRIGGER}
          >
            <span className="font-medium text-ink">
              {current?.name ?? t('neurocomment.listener.choose')}
            </span>
            <span className={`tb-ddchev flex shrink-0 text-ink-subtle ${open ? 'open' : ''}`}>
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="m6 9 6 6 6-6" />
              </svg>
            </span>
          </div>
          <div
            className={`tb-dd absolute inset-x-0 top-[calc(100%+5px)] z-20 rounded-[10px] border border-line bg-white p-1 shadow-[0_10px_30px_rgba(11,11,12,0.1)] ${open ? 'open' : ''}`}
          >
            {options.map((o) => (
              <div
                key={o.id}
                role="button"
                tabIndex={0}
                onClick={() => {
                  setPick(o.id);
                  setOpen(false);
                }}
                className="flex cursor-pointer items-center justify-between gap-2 rounded-[7px] px-[11px] py-[9px] text-[13px] transition-colors hover:bg-[#f2f6ff]"
              >
                <span className="font-medium">{o.name}</span>
                {o.id === pick ? (
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#0066ff"
                    strokeWidth="2.4"
                  >
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                ) : null}
              </div>
            ))}
          </div>
        </div>

        <div className="mt-[22px] flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
          >
            {t('neurocomment.modal.cancel')}
          </button>
          <button
            type="button"
            onClick={save}
            className={`rounded-full border px-5 py-[9px] text-[13px] font-semibold text-white ${saved ? 'border-success bg-success' : 'border-primary bg-primary'}`}
          >
            {saved ? (
              <span className="inline-flex items-center gap-[6px]">
                <span className="inline-flex [animation:swapin_0.3s_ease_both]">
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.4"
                  >
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                </span>
                <span className="inline-block [animation:swapin_0.3s_ease_0.09s_both]">
                  {t('neurocomment.modal.saved')}
                </span>
              </span>
            ) : (
              t('neurocomment.modal.save')
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
}
