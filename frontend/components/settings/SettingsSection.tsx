interface SettingsSectionProps {
  title: string;
  description?: React.ReactNode;
  /** Buttons for the top-right of the section. */
  actions?: React.ReactNode;
  children: React.ReactNode;
}

/** One Configuration section: a heading with its description, then the cards. Every page uses it. */
export function SettingsSection({ title, description, actions, children }: SettingsSectionProps) {
  return (
    <div className="cfg-sections">
      <header className="cfg-head">
        <div>
          <h2>{title}</h2>
          {description && <p className="hint">{description}</p>}
        </div>
        {actions && <div className="action-buttons">{actions}</div>}
      </header>
      {children}
    </div>
  );
}
