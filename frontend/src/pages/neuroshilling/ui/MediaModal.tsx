import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Input, Modal, Select } from '@/shared/ui';

import type { ScenarioDraft } from './scenarioDraft';

// Вложение к реплике: ссылка на сообщение, из которого прогон возьмёт медиа, и шаг, с
// которым оно уедет.
//
// Диалог, а не строка под темой: слот заполняют редко, а места он занимал столько же,
// сколько сама тема. Правки держатся ЛОКАЛЬНО и уходят в черновик только по «Прикрепить»:
// у диалога есть «Отмена», и отмена, которая ничего не отменяет, — худшая из кнопок.
export function MediaModal({
  draft,
  onDraft,
  onClose,
}: {
  draft: ScenarioDraft;
  onDraft: (draft: ScenarioDraft) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [link, setLink] = useState(draft.mediaMessageLink);
  const [position, setPosition] = useState(draft.mediaStepPosition);

  return (
    <Modal onClose={onClose} size="form" label={t('neuroshilling.scenario.media.toggle')}>
      <div className="border-b border-line-row px-2xl pb-lg pt-xl">
        <div className="type-dialog-title">{t('neuroshilling.scenario.media.toggle')}</div>
        <div className="mt-hair type-caption">{t('neuroshilling.scenario.media.hint')}</div>
      </div>

      <div className="flex flex-col gap-md px-2xl py-lg">
        <Input
          autoFocus
          size="sm"
          value={link}
          maxLength={500}
          placeholder={t('neuroshilling.scenario.media.placeholder')}
          aria-label={t('neuroshilling.scenario.media.label')}
          onChange={(event) => {
            setLink(event.target.value);
          }}
        />
        <Select
          value={position === null ? '' : String(position)}
          onChange={(value) => {
            setPosition(value === '' ? null : Number(value));
          }}
          options={[
            { value: '', label: t('neuroshilling.scenario.media.stepNone') },
            // Только сообщения: медиа едет отправкой самого шага, а реакция не отправляет
            // ничего, что могло бы его нести.
            ...draft.steps.flatMap((step, index) =>
              step.kind === 'message'
                ? [
                    {
                      value: String(index + 1),
                      label: t('neuroshilling.scenario.steps.position', { position: index + 1 }),
                    },
                  ]
                : [],
            ),
          ]}
          ariaLabel={t('neuroshilling.scenario.media.step')}
        />
      </div>

      <div className="flex items-center justify-end gap-sm border-t border-line-row px-2xl py-lg">
        <Button size="sm" onClick={onClose}>
          {t('neuroshilling.settings.cancel')}
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={() => {
            // Пустая ссылка снимает и шаг: позиция без ссылки — слот, который ничего не
            // несёт, и утверждение отказывает по нему же.
            const trimmed = link.trim();
            onDraft({
              ...draft,
              mediaMessageLink: trimmed,
              mediaStepPosition: trimmed ? position : null,
            });
            onClose();
          }}
        >
          {t('neuroshilling.scenario.media.attach')}
        </Button>
      </div>
    </Modal>
  );
}
