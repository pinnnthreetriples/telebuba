import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FOCUS_RING, HEADING_ICON_TILE } from '@/shared/design-system';
import { cn } from '@/shared/lib';
import { Button, Icon, Modal } from '@/shared/ui';

import { PERSONAS, personaFor, type WarmingFolder } from '../model/folders';

import { FolderSummary } from './FolderGrid';
import { PoolGeoNotice } from './GeoNotice';

// «В папку»: выбранные готовые аккаунты уходят в папку и стартуют с ЕЁ настройками —
// длительность, тип по смеси папки, устройство и окно первого захода. Своих настроек
// у аккаунта в этом окне нет: в этом и смысл папки.
export function AddToFolderModal({
  accountIds,
  initialFolderId,
  skipped,
  geoPairs = [],
  folders,
  startIndex,
  onCreateFolder,
  onClose,
  onConfirm,
}: {
  accountIds: string[];
  // Брошенный на папку аккаунт: папка уже выбрана.
  initialFolderId?: string;
  // Готовые к прогреву, но не готовые к старту (гейт готовности): не пойдут.
  skipped: number;
  // Пары стран `PHONE>PROXY` тех из `accountIds`, кто ждёт согласия на несовпадение
  // гео, — снятые при открытии окна. «Добавить и запустить» соглашается ровно на них.
  geoPairs?: readonly string[];
  folders: WarmingFolder[];
  // Сколько аккаунтов уже в папке: смесь типов продолжается, а не начинается заново.
  startIndex: (folderId: string) => number;
  onCreateFolder: () => void;
  onClose: () => void;
  onConfirm: (folderId: string, onlyGeoMatching: boolean) => void;
}) {
  const { t } = useTranslation();
  const [folderId, setFolderId] = useState<string | null>(
    initialFolderId ?? folders[0]?.id ?? null,
  );
  const folder = folders.find((f) => f.id === folderId);
  const title = t('warming.folders.add.title');
  const count = accountIds.length;
  const geoMismatch = geoPairs.length;

  // Сколько каких типов получит эта пачка — то, что оператор и хотел задать смесью.
  const split = (() => {
    if (!folder) return '';
    const from = startIndex(folder.id);
    const tally = { calm: 0, normal: 0, active: 0 };
    for (let i = 0; i < count; i++) tally[personaFor(folder.mix, from + i)] += 1;
    return PERSONAS.filter((p) => tally[p] > 0)
      .map((p) => `${t(`warming.persona.${p}.name`)} ${String(tally[p])}`)
      .join(' · ');
  })();

  return (
    <Modal onClose={onClose} size="form" label={title}>
      {(close) => (
        <div className="p-6">
          <div className="mb-1 flex items-start gap-3">
            <div className={HEADING_ICON_TILE}>
              <Icon name="folder" size={16} />
            </div>
            <div className="flex-1 type-h2">{title}</div>
          </div>
          <div className="mb-6 type-body text-content-muted">
            {t('warming.folders.add.subtitle', { count })}
            {skipped ? (
              <div className="mt-1 text-warning-deep">
                {t('warming.pool.skipped', { count: skipped })}
              </div>
            ) : null}
          </div>

          <div className="mb-2 type-body-medium">{t('warming.folders.add.pick')}</div>
          <div className="mb-4 flex flex-col gap-2">
            {folders.map((f) => {
              const on = f.id === folderId;
              return (
                <button
                  key={f.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => {
                    setFolderId(f.id);
                  }}
                  className={cn(
                    'flex items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors',
                    FOCUS_RING,
                    on
                      ? 'border-action-primary bg-info-tint text-info-strong'
                      : 'border-line bg-surface-card hover:border-line-strong',
                  )}
                >
                  <Icon name="folder" size={16} />
                  <span className="flex-1 type-body-medium">{f.name}</span>
                  <span className="type-small text-content-muted">
                    {t('warming.folders.daysValue', { count: f.days })}
                  </span>
                </button>
              );
            })}
            <Button variant="dashed" className="gap-2" onClick={onCreateFolder}>
              <Icon name="folder-plus" size={14} />
              {t('warming.folders.add.newFolder')}
            </Button>
          </div>

          {folder ? (
            <>
              <FolderSummary folder={folder} caption="" />
              <div className="mb-4 type-small text-content-muted">
                {t('warming.folders.add.split', { split })}
              </div>
            </>
          ) : null}

          {geoMismatch > 0 ? (
            <PoolGeoNotice count={geoMismatch} total={count} pairs={geoPairs} />
          ) : null}

          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="primary"
              disabled={!folder || count === 0}
              onClick={() => {
                if (folder) onConfirm(folder.id, false);
                onClose();
              }}
            >
              {t('warming.folders.add.start', { count })}
            </Button>
            {geoMismatch > 0 ? (
              <Button
                disabled={!folder || geoMismatch >= count}
                onClick={() => {
                  if (folder) onConfirm(folder.id, true);
                  onClose();
                }}
              >
                {t('warming.geo.onlyMatching')}
              </Button>
            ) : null}
            <Button onClick={close}>{t('warming.days.cancel')}</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
