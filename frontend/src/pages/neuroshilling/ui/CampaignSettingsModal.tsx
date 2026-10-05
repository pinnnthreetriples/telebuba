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
      <div className="flex items-center gap-3 border-b border-canvas px-6 pb-4 pt-6">
        <div className="min-w-0">
          <div className="truncate type-h2">{name}</div>
          <div className="mt-1 type-small">{t('neuroshilling.settings.subtitle')}</div>
        </div>
        <div className="flex-1" />
        {dirty ? (
          <span className="shrink-0 rounded-full bg-warning-tint px-3 py-1 text-small font-medium text-warning-deep">
            {t('neuroshilling.setup.unsaved')}
          </span>
        ) : null}
      </div>

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

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-canvas px-6 py-4">
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
      </div>
    </Modal>
  );
}
