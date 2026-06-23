interface SwitchFieldProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  description?: string;
}

export function SwitchField({ label, checked, onChange, description }: SwitchFieldProps) {
  return (
    <label className="switch-field">
      <span className="switch-field__copy">
        <span>{label}</span>
        {description ? <small>{description}</small> : null}
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="switch-field__control" aria-hidden="true" />
    </label>
  );
}
