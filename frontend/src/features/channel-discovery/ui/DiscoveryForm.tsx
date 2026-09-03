import type { DiscoveryAccountOption } from '@/shared/api';

import { canSubmit, type DiscoveryFormState } from '../model/discovery';
import { AccountPicker } from './AccountPicker';
import { DiscoveryFilters } from './DiscoveryFilters';
import { KeywordsField } from './KeywordsField';

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

// Композиция трёх блоков; кнопки — в подвале модалки. / Composition only.
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
  return (
    <form
      id={formId}
      className="flex flex-col gap-2xl"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit(form, accountIds) && !submitting) onSubmit();
      }}
    >
      <KeywordsField form={form} onChange={onChange} />
      <DiscoveryFilters form={form} onChange={onChange} />
      <AccountPicker
        accounts={accounts}
        selected={accountIds}
        loading={accountsLoading}
        errored={accountsErrored}
        onChange={(ids) => {
          onChange({ ...form, accountIds: ids });
        }}
      />
    </form>
  );
}
