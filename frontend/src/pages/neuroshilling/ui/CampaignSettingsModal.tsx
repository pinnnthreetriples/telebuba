import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Modal, ModalFooter, ModalHeader } from '@/shared/ui';

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
  saving,
  conflict,
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
  saving: boolean;
  conflict: boolean;
  onSave: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Modal
      onClose={() => {
        if (!saving) onClose();
      }}
      size="table"
      label={t('neuroshilling.settings.title', { name })}
    >
      <ModalHeader title={name} subtitle={t('neuroshilling.settings.subtitle')}>
        <div className="flex-1" />
        {dirty ? (
          <span className="shrink-0 rounded-full bg-warning-tint px-3 py-1 text-small font-medium text-warning-deep">
            {t('neuroshilling.setup.unsaved')}
          </span>
        ) : null}
      </ModalHeader>

      {/* Своей прокрутки нет: у варианта `center` её держит оверлей (`overflow-y-auto`
          на подложке), а карточка растёт по содержимому. Второй скролл-контейнер внутри
          дал бы вложенную цепочку прокрутки — ровно то, от чего оверлей и уводит. */}
      {conflict ? (
        <p role="alert" className="mx-6 mt-4 type-body text-danger">
          {t('neuroshilling.settings.conflict')}
        </p>
      ) : null}
      <fieldset disabled={saving} className="m-0 flex min-w-0 flex-col gap-6 border-0 px-6 py-6">
        {children}
      </fieldset>

      <ModalFooter>
        <Button size="sm" onClick={onClose} disabled={saving}>
          {t('neuroshilling.settings.cancel')}
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={busy || !dirty || conflict}
          loading={saving}
          onClick={onSave}
        >
          {t('neuroshilling.settings.save')}
        </Button>
      </ModalFooter>
    </Modal>
  );
}
