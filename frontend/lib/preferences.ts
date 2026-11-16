export interface UserPreferences {
  locale: string;
  time_zone: string;
  density: 'comfortable' | 'compact';
  contrast: 'standard' | 'high';
  motion: 'system' | 'reduce';
  theme: 'system' | 'light' | 'dark';
  updated_at?: string | null;
}

export const DEFAULT_PREFERENCES: UserPreferences = {
  locale: 'en-US',
  time_zone: 'UTC',
  density: 'comfortable',
  contrast: 'standard',
  motion: 'system',
  theme: 'system',
};

export function applyUserPreferences(preferences: UserPreferences) {
  try {
    localStorage.setItem('nimbus_preferences', JSON.stringify(preferences));
  } catch {
    /* storage unavailable */
  }
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.dataset.density = preferences.density;
  root.dataset.contrast = preferences.contrast;
  root.dataset.motion = preferences.motion;
  root.dataset.timeZone = preferences.time_zone;
  window.dispatchEvent(new CustomEvent('nimbus:preference-theme', { detail: preferences.theme }));
}

export function currentUserPreferences(): UserPreferences {
  try {
    const saved = localStorage.getItem('nimbus_preferences');
    return saved ? { ...DEFAULT_PREFERENCES, ...JSON.parse(saved) } : DEFAULT_PREFERENCES;
  } catch {
    return DEFAULT_PREFERENCES;
  }
}

export function formatNumber(value: number, options?: Intl.NumberFormatOptions) {
  return new Intl.NumberFormat(currentUserPreferences().locale, options).format(value);
}

export function formatDateTime(value: Date | string, options?: Intl.DateTimeFormatOptions) {
  const preferences = currentUserPreferences();
  return new Intl.DateTimeFormat(preferences.locale, {
    timeZone: preferences.time_zone,
    ...options,
  }).format(typeof value === 'string' ? new Date(value) : value);
}

export function formatBusinessDate(value: string, options?: Intl.DateTimeFormatOptions) {
  const preferences = currentUserPreferences();
  return new Intl.DateTimeFormat(preferences.locale, {
    timeZone: 'UTC',
    ...options,
  }).format(new Date(`${value}T00:00:00Z`));
}
