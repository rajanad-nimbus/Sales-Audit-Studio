'use client';

import { useState } from 'react';

export default function SettingsPage() {
  const [settings, setSettings] = useState({
    app_name: 'ZeTSA',
    theme: 'auto',
    notifications: true,
    email_notifications: false,
    api_rate_limit: 1000,
  });

  const [saved, setSaved] = useState(false);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value, type } = e.target as any;
    setSettings((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? (e.target as HTMLInputElement).checked : value,
    }));
  };

  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      {saved && <div className="alert alert-success">Settings saved successfully!</div>}

      <div className="settings-container">
        <div className="settings-section">
          <h2>Application Settings</h2>

          <div className="form-group">
            <label htmlFor="app_name">Application Name</label>
            <input
              type="text"
              id="app_name"
              name="app_name"
              value={settings.app_name}
              onChange={handleChange}
              placeholder="Enter application name"
            />
          </div>

          <div className="form-group">
            <label htmlFor="theme">Theme</label>
            <select id="theme" name="theme" value={settings.theme} onChange={handleChange}>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
              <option value="auto">Auto (System)</option>
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="api_rate_limit">API Rate Limit (requests/hour)</label>
            <input
              type="number"
              id="api_rate_limit"
              name="api_rate_limit"
              value={settings.api_rate_limit}
              onChange={handleChange}
              placeholder="Enter rate limit"
            />
          </div>
        </div>

        <div className="settings-section">
          <h2>Notification Preferences</h2>

          <div className="checkbox-group">
            <input
              type="checkbox"
              id="notifications"
              name="notifications"
              checked={settings.notifications}
              onChange={handleChange}
            />
            <label htmlFor="notifications">Enable notifications</label>
            <p className="checkbox-hint">Receive updates about system events</p>
          </div>

          <div className="checkbox-group">
            <input
              type="checkbox"
              id="email_notifications"
              name="email_notifications"
              checked={settings.email_notifications}
              onChange={handleChange}
            />
            <label htmlFor="email_notifications">Email notifications</label>
            <p className="checkbox-hint">Send important updates via email</p>
          </div>
        </div>

        <div className="settings-section">
          <h2>About</h2>
          <div className="info-card">
            <p>
              <strong>Application:</strong> ZeTSA (FastAPI + Next.js)
            </p>
            <p>
              <strong>Version:</strong> 1.0.0
            </p>
            <p>
              <strong>Backend:</strong> FastAPI 0.142.2
            </p>
            <p>
              <strong>Frontend:</strong> Next.js 16.3.8
            </p>
            <p>
              <strong>Database:</strong> PostgreSQL 18
            </p>
          </div>
        </div>

        <div className="settings-actions">
          <button className="btn-primary btn-lg" onClick={handleSave}>
            Save Settings
          </button>
          <button className="btn-secondary btn-lg">Reset to Defaults</button>
        </div>
      </div>
    </div>
  );
}
