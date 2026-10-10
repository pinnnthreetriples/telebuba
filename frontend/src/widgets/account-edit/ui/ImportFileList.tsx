import { useTranslation } from 'react-i18next';

import { Icon, IconButton, Spinner } from '@/shared/ui';

import type { BulkFile } from './useBulkImport';

// One row per picked file, styled as the wizard's single-file card; a summary
// line above once there is more than one. A failed row says why in one short
// line and offers two icons: retry, and × that drops the row on the spot (nothing
// was created server-side, so there is nothing to confirm).
export function ImportFileList({
  files,
  onRetry,
  onRemove,
}: {
  files: BulkFile[];
  onRetry: (id: number) => void;
  onRemove: (id: number) => void;
}) {
  const { t } = useTranslation();
  const ok = files.filter((f) => f.state === 'ok').length;

  const verdict = (file: BulkFile) => {
    if (file.state === 'importing') return t('accounts.addWizard.importing');
    if (file.state === 'error')
      return t(`accounts.addWizard.importFailure.${file.failure ?? 'other'}`);
    return file.accountIds.length > 1
      ? t('accounts.addWizard.importedMany', { count: file.accountIds.length })
      : t('accounts.addWizard.imported');
  };

  return (
    <div className="flex flex-col gap-3">
      {files.length > 1 && (
        <div className="type-small">
          {t('accounts.addWizard.importSummary', { ok, total: files.length })}
        </div>
      )}
      {files.map((file) => (
        <div
          key={file.id}
          className="tb-fadeup rounded-md border border-line bg-surface-card px-3 py-3"
        >
          <div className="flex items-center gap-3">
            <div className="flex size-thumbnail shrink-0 items-center justify-center rounded-md bg-canvas text-content-muted">
              <Icon name="file" size={18} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate type-body-medium">{file.name}</div>
              <div
                className={`text-small ${file.state === 'error' ? 'text-danger' : file.state === 'ok' ? 'text-success-deep' : 'text-content-subtle'}`}
              >
                {verdict(file)}
              </div>
            </div>
            {file.state === 'importing' ? (
              <Spinner className="m-1" />
            ) : file.state === 'error' ? (
              <>
                <IconButton
                  tone="primary"
                  aria-label={t('accounts.addWizard.retry')}
                  title={t('accounts.addWizard.retry')}
                  onClick={() => {
                    onRetry(file.id);
                  }}
                >
                  <Icon name="refresh" size={16} />
                </IconButton>
                <IconButton
                  tone="danger"
                  aria-label={t('accounts.addWizard.removeFile')}
                  title={t('accounts.addWizard.removeFile')}
                  onClick={() => {
                    onRemove(file.id);
                  }}
                >
                  <Icon name="close" size={16} />
                </IconButton>
              </>
            ) : (
              <span className="tb-pop m-1 inline-flex text-success-deep">
                <Icon name="check-circle" size={18} />
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
