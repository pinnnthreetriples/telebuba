import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountWebWindowsQueryOptions,
  closeAccountWebWindowsMutation,
  openAccountMobileMutation,
  openAccountWebMutation,
} from '@/entities/account';
import { toastError, toastSuccess } from '@/shared/ui';

// Each Telegram Web window is a full Chrome process; past this many the operator is
// told to close some.
const WEB_WINDOWS_WARN_AT = 8;

export function useAccountLaunchers() {
  const { t } = useTranslation();
  const [openWebBusyIds, setOpenWebBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [openMobileBusyIds, setOpenMobileBusyIds] = useState<ReadonlySet<string>>(new Set());

  const openWeb = useMutation(openAccountWebMutation());
  const openMobile = useMutation(openAccountMobileMutation());
  const webWindows = useQuery(accountWebWindowsQueryOptions());
  const closeWebWindows = useMutation(closeAccountWebWindowsMutation());

  const openWebWindows = webWindows.data?.count ?? 0;
  // Once per crossing: warning again on every open past the threshold is noise.
  // Re-armed whenever the count is seen below it (a close-all, a window closed by hand).
  const webWindowsWarned = useRef(false);
  useEffect(() => {
    if (openWebWindows < WEB_WINDOWS_WARN_AT) webWindowsWarned.current = false;
  }, [openWebWindows]);

  const onOpenWeb = (accountId: string) => {
    setOpenWebBusyIds((ids) => new Set(ids).add(accountId));
    void openWeb
      .mutateAsync({ path: { account_id: accountId } })
      .then((result) => {
        if (!result.signed_in) {
          toastError(
            t(
              result.reason
                ? `accounts.openWeb.reason.${result.reason}`
                : 'accounts.openWeb.notSignedIn',
            ),
          );
        }
      })
      .catch(() => undefined)
      .finally(() => {
        setOpenWebBusyIds((ids) => {
          const next = new Set(ids);
          next.delete(accountId);
          return next;
        });
        // Refetched even after a refusal: a failed open can still have dropped a window.
        void webWindows.refetch().then(({ data }) => {
          const count = data?.count ?? 0;
          if (count < WEB_WINDOWS_WARN_AT || webWindowsWarned.current) return;
          webWindowsWarned.current = true;
          toastError(t('accounts.webWindows.tooMany', { count }));
        });
      });
  };

  const onCloseWebWindows = () => {
    void closeWebWindows
      .mutateAsync({})
      .then((closed) => {
        toastSuccess(t('accounts.webWindows.closed', { count: closed.count }));
      })
      .catch(() => undefined)
      .finally(() => {
        void webWindows.refetch();
      });
  };

  const onOpenMobile = (accountId: string) => {
    setOpenMobileBusyIds((ids) => new Set(ids).add(accountId));
    void openMobile
      .mutateAsync({ path: { account_id: accountId } })
      .then((result) => {
        if (result.signed_in) {
          toastSuccess(t('accounts.openMobile.started'));
        } else {
          toastError(
            t(
              result.message
                ? `accounts.openMobile.reason.${result.message}`
                : 'accounts.openMobile.twofaRequired',
            ),
          );
        }
      })
      .catch(() => undefined)
      .finally(() => {
        setOpenMobileBusyIds((ids) => {
          const next = new Set(ids);
          next.delete(accountId);
          return next;
        });
      });
  };

  return {
    openWebBusyIds,
    openMobileBusyIds,
    openWebWindows,
    closingWebWindows: closeWebWindows.isPending,
    onOpenWeb,
    onCloseWebWindows,
    onOpenMobile,
  };
}
