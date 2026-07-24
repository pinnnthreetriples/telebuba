import { useState } from 'react';

import { accountInitials } from '../model/displayName';

// Shared account avatar: the cached Telegram profile photo when captured (served
// by the cacheable /avatar endpoint, ?v=etag makes it immutable), degrading to
// the initials mono-avatar on absence or a load error. Used by the accounts
// table and every warming card so photo/initials behave identically everywhere.
export function AccountAvatar({
  account,
  className,
  fallbackClassName,
}: {
  account: {
    account_id: string;
    avatar_etag?: string | null;
    first_name?: string | null;
    last_name?: string | null;
    phone?: string | null;
  };
  className: string; // sizing/shape shared by img and fallback
  fallbackClassName: string; // initials-circle styling: bg + text colour + size
}) {
  // Track WHICH etag failed to load, not a bare boolean — the component stays
  // mounted across refetches, so a transient error must not hide a later-
  // recovered photo. A new avatar_etag makes `broken` recompute to false.
  const [failedEtag, setFailedEtag] = useState<string | null>(null);
  const broken = failedEtag !== null && failedEtag === account.avatar_etag;
  if (account.avatar_etag && !broken) {
    return (
      <img
        src={`/api/v1/accounts/${account.account_id}/avatar?v=${account.avatar_etag}`}
        alt=""
        loading="lazy"
        decoding="async"
        onError={() => {
          setFailedEtag(account.avatar_etag ?? null);
        }}
        className={`${className} object-cover`}
      />
    );
  }
  return (
    <div className={`${className} flex items-center justify-center ${fallbackClassName}`}>
      {accountInitials(account)}
    </div>
  );
}
