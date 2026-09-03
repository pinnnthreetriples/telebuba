import { useTranslation } from 'react-i18next';

import type { DiscoveryAccountOption } from '@/shared/api';

import { canSubmit, type DiscoveryFormState } from '../model/discovery';
import { AccountPicker } from './AccountPicker';
import { DiscoveryFilters } from './DiscoveryFilters';
import { Eyebrow } from './FormRow';
import { KeywordsField } from './KeywordsField';

const P = 'neurocomment.modal.discovery.form';

type Props = {
  form: DiscoveryFormState;
  // The submit button lives in the modal footer and reaches the form by `form={formId}`.
  formId: string;
  accounts: readonly DiscoveryAccountOption[];
  accountsLoading: boolean;
  accountsErrored: boolean;
  // `effectiveAccountIds(form.accountIds, accounts)` — resolved by the owner, so the
  // form, the footer button and the request all read the same list.
  accountIds: string[];
  submitting: boolean;
  onChange: (form: DiscoveryFormState) => void;
  onSubmit: () => void;
};

// Композиция: «Запрос» — ключевые слова и аккаунты в одной строке, ниже фильтры; кнопки —
// в подвале модалки. / Composition only: the query row, then the filters.
export function DiscoveryForm({
  form,
  formId,
  accounts,
  accountsLoading,
  accountsErrored,
  accountIds,
  submitting,
  onChange,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  return (
    <form
      id={formId}
      className="flex flex-col gap-2xl"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit(form, accountIds) && !submitting) onSubmit();
      }}
    >
      <section>
        <Eyebrow title={t(`${P}.sections.query`)} />
        {/* The same two columns as the filters below, so the picker sits under the
            right-hand filter column and the keywords under the left. */}
        <div className="grid gap-xl sm:grid-cols-2 sm:gap-2xl">
          <KeywordsField form={form} onChange={onChange} />
          <AccountPicker
            accounts={accounts}
            selected={accountIds}
            loading={accountsLoading}
            errored={accountsErrored}
            onChange={(ids) => {
              onChange({ ...form, accountIds: ids });
            }}
          />
        </div>
      </section>
      <DiscoveryFilters form={form} onChange={onChange} />
    </form>
  );
}
