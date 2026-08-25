import { Input } from '@/shared/ui';

const FIELD_LABEL = 'mb-tight block text-body font-medium text-ink-body';

// Stays inline, both halves. The crossed-out eye is this file's own transcription
// and is drawn nowhere else, so <Icon> can only take the open one — and the two
// swap in the same slot, where a 17px state and an 18px state would step.
function EyeIcon({ off }: { off: boolean }) {
  return off ? (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 8 10 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
      <path d="M1 1l22 22" />
      <path d="M6.61 6.61A13.5 13.5 0 0 0 2 12s3 8 10 8a9.7 9.7 0 0 0 5.39-1.61" />
    </svg>
  ) : (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <path d="M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

// One masked API-key input (Gemini or OpenAI): password field + show/hide toggle
// + a "clear stored key" affordance. Blank = keep; clear = wipe the stored key.
export function ApiKeyField({
  label,
  value,
  show,
  keySet,
  placeholder,
  toggleLabel,
  clearLabel,
  onChange,
  onToggleShow,
  onClear,
}: {
  label: string;
  value: string;
  show: boolean;
  keySet: boolean;
  placeholder: string;
  toggleLabel: string;
  clearLabel: string;
  onChange: (value: string) => void;
  onToggleShow: () => void;
  onClear: () => void;
}) {
  return (
    <label className="block">
      <span className={FIELD_LABEL}>{label}</span>
      <div className="flex gap-sm">
        <Input
          className="flex-1 font-mono"
          type={show ? 'text' : 'password'}
          // A provider API key, not a credential of this origin: `new-password`
          // is the token browsers honour as "do not fill" on a password input.
          autoComplete="new-password"
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
          }}
          placeholder={placeholder}
        />
        <button
          type="button"
          aria-label={toggleLabel}
          onClick={onToggleShow}
          className="flex w-action items-center justify-center rounded-lg border border-line bg-white text-ink-muted transition-colors hover:border-primary-line hover:bg-primary-tint hover:text-primary-deep"
        >
          <EyeIcon off={show} />
        </button>
      </div>
      {keySet && (
        <button
          type="button"
          onClick={onClear}
          className="mt-md text-body font-medium text-danger transition-colors hover:underline"
        >
          {clearLabel}
        </button>
      )}
    </label>
  );
}
