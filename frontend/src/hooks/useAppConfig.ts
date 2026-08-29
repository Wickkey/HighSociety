// Fetches /api/app_config once and caches it module-wide -- every mount of
// the login screen (or anything else that ever needs these values) reuses
// the same promise instead of re-fetching. Mirrors the old frontend's
// server-templated ga_measurement_id/google_client_id, just resolved at
// runtime instead of baked into the HTML (see web_server.py's api_app_config).
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { AppConfig } from '../types/api';

let configPromise: Promise<AppConfig> | null = null;
function getConfig(): Promise<AppConfig> {
  if (!configPromise) configPromise = api.config().catch(() => ({ gaMeasurementId: null, googleClientId: null }));
  return configPromise;
}

export function useAppConfig(): AppConfig | null {
  const [config, setConfig] = useState<AppConfig | null>(null);
  useEffect(() => {
    let cancelled = false;
    getConfig().then((c) => { if (!cancelled) setConfig(c); });
    return () => { cancelled = true; };
  }, []);
  return config;
}
