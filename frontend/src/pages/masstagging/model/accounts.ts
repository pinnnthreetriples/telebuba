import type { AccountRead } from '@/shared/api';

// A spam-blocked account stays on the roster but never works: the people it tags get no
// push. Read off the account's stored verdict, the one the engine reads too.
export function isSpamBlocked(account: AccountRead | undefined): boolean {
  return account?.spam_status === 'limited';
}
