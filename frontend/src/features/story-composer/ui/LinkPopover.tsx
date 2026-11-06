import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { surface } from '@/shared/design-system';
import { Button, Input } from '@/shared/ui';

import { normalizeUrl } from '../model/url';

// The inline URL prompt under the toolbar. It owns only the draft and its validation;
// what to do with the normalised address is the editor's business.
export function LinkPopover({
  initialUrl,
  onApply,
  onRemove,
  onCancel,
}: {
  initialUrl: string;
  onApply: (url: string) => void;
  onRemove: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(initialUrl);
  const [invalid, setInvalid] = useState(false);

  const submit = () => {
    const url = normalizeUrl(draft);
    if (url === null) {
      setInvalid(true);
      return;
    }
    onApply(url);
  };

  return (
    <div
      role="group"
      aria-label={t('storyComposer.caption.link')}
      className={`${surface('panel')} flex flex-col gap-2 p-3`}
    >
      <Input
        autoFocus
        value={draft}
        invalid={invalid}
        aria-label={t('storyComposer.caption.linkUrl')}
        placeholder="https://"
        inputMode="url"
        onChange={(event) => {
          setDraft(event.target.value);
          setInvalid(false);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            submit();
          } else if (event.key === 'Escape') {
            event.preventDefault();
            onCancel();
          }
        }}
      />
      {invalid ? (
        <p role="alert" className="type-small text-danger-deep">
          {t('storyComposer.caption.linkInvalid')}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={submit}>
          {t('storyComposer.caption.linkApply')}
        </Button>
        {initialUrl !== '' ? (
          <Button size="sm" variant="secondary" onClick={onRemove}>
            {t('storyComposer.caption.linkRemove')}
          </Button>
        ) : null}
        <Button size="sm" variant="secondary" onClick={onCancel}>
          {t('storyComposer.caption.linkCancel')}
        </Button>
      </div>
    </div>
  );
}
