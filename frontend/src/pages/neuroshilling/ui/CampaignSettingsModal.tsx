import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Modal } from '@/shared/ui';

// Оболочка диалога настроек кампании: шапка с именем, прокручиваемое тело и подвал с
// сохранением. Всё, что внутри, кладёт страница.
//
// Именно оболочка, а не компонент, знающий про ростер, цели, роли и шаги. Такой компонент
// пришлось бы кормить объединением пропсов трёх редакторов — под сорок штук, каждый из
// которых он только передаёт дальше. Прокладка, которая ничего не решает, но обязана
// меняться при каждой правке любого из трёх, — это не слой, а лишний файл в каждом диффе.
export function CampaignSettingsModal({
  name,
  dirty,
  busy,
  onSave,
  onClose,
  children,
}: {
  name: string;
  // Есть ли что сохранять. Собирается страницей из ОБОИХ черновиков — сценария и
  // настроек: диалог один, кнопка сохранения одна, и «сохранить» здесь значит «сохранить
  // всё, что тронуто».
  dirty: boolean;
  busy: boolean;
  onSave: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Modal onClose={onClose} size="table" label={t('neuroshilling.settings.title', { name })}>
      <div className="flex items-center gap-md border-b border-line-row px-2xl pb-lg pt-xl">
        <div className="min-w-0">
          <div className="truncate type-dialog-title">{name}</div>
          <div className="mt-hair type-caption">{t('neuroshilling.settings.subtitle')}</div>
        </div>
        <div className="flex-1" />
        {dirty ? (
          <span className="shrink-0 rounded-full bg-warning-tint px-md py-xs text-tiny font-semibold text-warning-deep">
            {t('neuroshilling.setup.unsaved')}
          </span>
        ) : null}
      </div>

      {/* Своей прокрутки нет: у варианта `center` её держит оверлей (`overflow-y-auto`
          на подложке), а карточка растёт по содержимому. Второй скролл-контейнер внутри
          дал бы вложенную цепочку прокрутки — ровно то, от чего оверлей и уводит. */}
      <div className="flex flex-col gap-2xl px-2xl py-xl">{children}</div>

      <div className="flex flex-wrap items-center justify-end gap-sm border-t border-line-row px-2xl py-lg">
        <Button size="sm" onClick={onClose}>
          {t('neuroshilling.settings.cancel')}
        </Button>
        <Button variant="primary" size="sm" disabled={busy || !dirty} onClick={onSave}>
          {t('neuroshilling.settings.save')}
        </Button>
      </div>
    </Modal>
  );
}
