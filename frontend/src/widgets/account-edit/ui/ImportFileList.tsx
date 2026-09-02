import { useTranslation } from 'react-i18next';

import { Button, Icon, Spinner } from '@/shared/ui';

import type { BulkFile } from './useBulkImport';

// One row per picked file, styled as the wizard's single-file card; a summary
// line above once there is more than one.
export function ImportFileList({
  files,
  onRetry,
}: {
  files: BulkFile[];
  onRetry: (index: number) => void;
}) {
  const { t } = useTranslation();
  const ok = files.filter((f) => f.state === 'ok').length;

  const verdict = (file: BulkFile) => {
    if (file.state === 'importing') return t('accounts.addWizard.importing');
    if (file.state === 'error') return t('accounts.addWizard.importError');
    return file.accountIds.length > 1
      ? t('accounts.addWizard.importedMany', { count: file.accountIds.length })
      : t('accounts.addWizard.imported');
  };

  return (
    <div className="flex flex-col gap-md">
      {files.length > 1 && (
        <div className="type-caption">
          {t('accounts.addWizard.importSummary', { ok, total: files.length })}
        </div>
      )}
      {files.map((file, index) => (
        <div
          key={index}
          className="tb-fadeup rounded-lg border border-line bg-surface-card px-md py-md"
        >
          <div className="flex items-center gap-md">
            <div className="flex size-thumbnail shrink-0 items-center justify-center rounded-lg bg-canvas text-content-muted">
              <Icon name="file" size={18} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate type-item-title">{file.name}</div>
              <div
                className={`mt-px text-tiny ${file.state === 'error' ? 'text-danger' : file.state === 'ok' ? 'text-success-deep' : 'text-content-subtle'}`}
              >
                {verdict(file)}
              </div>
            </div>
            {file.state === 'importing' ? (
              <Spinner className="m-tight" />
            ) : file.state === 'error' ? (
              <>
                <Button
                  size="sm"
                  onClick={() => {
                    onRetry(index);
                  }}
                >
                  {t('accounts.addWizard.retry')}
                </Button>
                <span className="m-xs inline-flex text-danger">
                  <Icon name="x-circle" size={18} />
                </span>
              </>
            ) : (
              <span className="tb-pop m-xs inline-flex text-success-deep">
                <Icon name="check-circle" size={18} />
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
