import { useTranslation } from 'react-i18next';

import type { DiscoveryCandidate } from '@/shared/api';
import { Badge, type BadgeTone } from '@/shared/ui';

// Locale-neutral codes from the server; an unmapped one renders as itself, like the
// source and reason labels do, so a code added later degrades to text, not to a key.
const TRAITS = 'neurocomment.modal.discovery.results';

// Open is the one access mode the campaign can comment in without anyone's approval.
function accessTone(access: string): BadgeTone {
  return access === 'open' ? 'success' : 'warning';
}

/** What kind of place a row is — group or channel, how it is entered, its language.
 *
 * Every badge appears only on an explicit value: rows stored before these fields
 * existed carry none, and an empty cell there is honest where a guessed "channel" would
 * not be.
 */
export function TraitsCell({ candidate }: { candidate: DiscoveryCandidate }) {
  const { t } = useTranslation();
  const { kind, access, language } = candidate;
  return (
    <div className="flex flex-wrap gap-tight">
      {kind ? <Badge size="xs">{t(`${TRAITS}.kind.${kind}`, { defaultValue: kind })}</Badge> : null}
      {access ? (
        <Badge size="xs" tone={accessTone(access)}>
          {t(`${TRAITS}.access.${access}`, { defaultValue: access })}
        </Badge>
      ) : null}
      {language ? (
        <Badge size="xs">{t(`${TRAITS}.language.${language}`, { defaultValue: language })}</Badge>
      ) : null}
    </div>
  );
}
