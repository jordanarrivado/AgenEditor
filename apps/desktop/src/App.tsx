import React, { Suspense, useMemo } from 'react';
import { RouterProvider, createBrowserRouter } from 'react-router-dom';
import { isTauri } from '@tauri-apps/api/core';
import { AppProviders } from './app/providers';
import { routes } from './app/router';

const LiveWorkspace = React.lazy(async () => ({ default: (await import('./features/live/SimpleWorkspace')).SimpleWorkspace }));

export default function App() {
  const router = useMemo(() => createBrowserRouter(routes), []);
  if (isTauri()) return <Suspense fallback={<div className="p-6">Connecting to the local engine…</div>}><LiveWorkspace /></Suspense>;
  return (
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  );
}
