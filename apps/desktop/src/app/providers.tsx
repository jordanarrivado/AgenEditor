import React, { useMemo } from 'react';
import { AppDataProvider } from './AppDataContext';
import { SettingsProvider, type UserSettings } from './SettingsContext';
import { createDefaultRepository } from '../services/repositories';
import type { ProjectRepository } from '../services/projectRepository';

/** All app-level providers. Tests pass an isolated repository. */
export const AppProviders: React.FC<{
  children: React.ReactNode;
  repo?: ProjectRepository;
  persistsLocally?: boolean;
  settings?: Partial<UserSettings>;
}> = ({ children, repo, persistsLocally, settings }) => {
  const resolved = useMemo(() => {
    if (repo) return { repo, persists: persistsLocally ?? false };
    const r = createDefaultRepository();
    return { repo: r, persists: r.persistsLocally };
  }, [repo, persistsLocally]);
  return (
    <SettingsProvider initial={settings}>
      <AppDataProvider repo={resolved.repo} persistsLocally={resolved.persists}>
        {children}
      </AppDataProvider>
    </SettingsProvider>
  );
};
