import { useTranslation } from 'react-i18next';

import { DEVICE_TYPES, type DeviceChoice, type DeviceType } from '@/entities/account';
import { SegmentedControl } from '@/shared/ui';

const CHOICES: readonly DeviceChoice[] = ['desktop', 'mobile', 'both'];

// Step 1's device pick: which device the new account signs in as, and whether the
// other one is added as a second sign-in later in the wizard. Desktop is the default;
// a tdata archive is a desktop client's data, so "mobile only" is not offered for it.
// For an uploaded .session with "both" the operator also says which device the FILE
// is — the other one is the sign-in the wizard adds.
export function DeviceChoicePicker({
  method,
  value,
  fileDevice,
  disabled,
  onChange,
  onFileDevice,
}: {
  method: 'session' | 'tdata' | 'phone';
  value: DeviceChoice;
  fileDevice: DeviceType;
  disabled: boolean;
  onChange: (value: DeviceChoice) => void;
  onFileDevice: (value: DeviceType) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-2">
      <div className="type-small-medium">{t('accounts.addWizard.deviceLabel')}</div>
      <SegmentedControl
        variant="outline"
        value={value}
        disabled={disabled}
        ariaLabel={t('accounts.addWizard.deviceLabel')}
        options={CHOICES.map((choice) => {
          const refused = method === 'tdata' && choice === 'mobile';
          return {
            value: choice,
            label: t(`accounts.addWizard.deviceChoice.${choice}`),
            disabled: refused,
            title: refused ? t('accounts.addWizard.deviceTdataDesktop') : undefined,
          };
        })}
        onChange={onChange}
      />
      {method === 'session' && value === 'both' ? (
        <>
          <div className="type-small-medium">{t('accounts.addWizard.deviceFileLabel')}</div>
          <SegmentedControl
            variant="outline"
            value={fileDevice}
            disabled={disabled}
            ariaLabel={t('accounts.addWizard.deviceFileLabel')}
            options={DEVICE_TYPES.map((device) => ({
              value: device,
              label: t(`accounts.devices.${device}`),
            }))}
            onChange={onFileDevice}
          />
        </>
      ) : null}
    </div>
  );
}
