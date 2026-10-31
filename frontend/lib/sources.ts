export interface Source {
  source_system: string;
  enabled: boolean;
  in_daily_batch: boolean;
  manual_sync: boolean;
  pulled: boolean;
  configured: boolean | null;
  last_received_at: string | null;
  quarantined: number;
  sync_frequency: 'daily' | 'weekly' | null;
  sync_time: string | null;
  sync_weekday: number | null;
  sync_timezone: string | null;
  schedule: string | null;
  last_scheduled_run_at: string | null;
  last_scheduled_status: string | null;
  last_scheduled_message: string | null;
  next_run_at: string | null;
}
