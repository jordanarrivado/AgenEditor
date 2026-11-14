/**
 * Persistent UI preferences (theme, motion, editing defaults). Stored in
 * localStorage when available; always falls back to defaults.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { AspectRatio } from '../types';
import { DEFAULT_ROLE_PROFILE, type RoleProfile } from '../utils/agentRoles';

export interface UserSettings {
  theme: 'dark' | 'light';
  reducedMotion: boolean;
  language: 'en';
  hasSeenWelcome: boolean;
  defaultAspectRatio: AspectRatio;
  defaultDurationSeconds: number;
  defaultFps: 24 | 30 | 60;
  defaultCaptionPresetId: string;
  defaultSnapping: boolean;
  previewQuality: 'draft' | 'standard';
  allowExternalSharing: boolean; // mock toggle — no effect until engine integration
  agentRoles: RoleProfile;
}

export const DEFAULT_SETTINGS: UserSettings = {
  theme: 'dark',
  reducedMotion: false,
  language: 'en',
  hasSeenWelcome: false,
  defaultAspectRatio: '9:16',
  defaultDurationSeconds: 40,
  defaultFps: 30,
  defaultCaptionPresetId: 'preset_bold_yellow',
  defaultSnapping: true,
  previewQuality: 'standard',
  allowExternalSharing: false,
  agentRoles: DEFAULT_ROLE_PROFILE,
};

const KEY = 'ageneditor_settings_v1';

function readSettings(): UserSettings {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<UserSettings>;
    return { ...DEFAULT_SETTINGS, ...parsed, agentRoles: { ...DEFAULT_ROLE_PROFILE, ...parsed.agentRoles } };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

interface SettingsCtx {
  settings: UserSettings;
  update: (patch: Partial<UserSettings>) => void;
  reset: () => void;
}

const Ctx = createContext<SettingsCtx | null>(null);

export const SettingsProvider: React.FC<{ children: React.ReactNode; initial?: Partial<UserSettings> }> = ({
  children,
  initial,
}) => {
  const [settings, setSettings] = useState<UserSettings>(() => ({ ...readSettings(), ...initial }));

  useEffect(() => {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(settings));
    } catch {
      /* storage unavailable — settings last for this session only */
    }
    const root = document.documentElement;
    root.dataset.theme = settings.theme;
    root.dataset.reducedMotion = String(settings.reducedMotion);
  }, [settings]);

  const update = useCallback((patch: Partial<UserSettings>) => setSettings((s) => ({ ...s, ...patch })), []);
  const reset = useCallback(() => setSettings({ ...DEFAULT_SETTINGS, hasSeenWelcome: true }), []);
  const value = useMemo(() => ({ settings, update, reset }), [settings, update, reset]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

export function useSettings(): SettingsCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error('useSettings must be used inside SettingsProvider');
  return v;
}

/** True when either the OS or the in-app setting asks for reduced motion. */
export function usePrefersReducedMotion(): boolean {
  const { settings } = useSettings();
  const [os, setOs] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false
  );
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setOs(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  return os || settings.reducedMotion;
}
