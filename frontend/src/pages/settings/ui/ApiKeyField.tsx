const INPUT =
  'tb-time w-full rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none';
const FIELD_LABEL = 'mb-[6px] block text-[12px] font-medium text-[#3a3a3a]';

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
      <div className="flex gap-2">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
          }}
          placeholder={placeholder}
          className={`${INPUT} flex-1 font-mono`}
        />
        <button
          type="button"
          aria-label={toggleLabel}
          onClick={onToggleShow}
          className="flex w-[42px] items-center justify-center rounded-[10px] border border-line-input bg-white text-ink-muted transition-colors hover:border-[#cbd7ec] hover:bg-[#f2f6ff] hover:text-primary"
        >
          <EyeIcon off={show} />
        </button>
      </div>
      {keySet && (
        <button
          type="button"
          onClick={onClear}
          className="mt-[9px] text-[12px] font-medium text-danger transition-colors hover:underline"
        >
          {clearLabel}
        </button>
      )}
    </label>
  );
}
