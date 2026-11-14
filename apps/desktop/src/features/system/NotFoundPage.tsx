import React from 'react';
import { Link, isRouteErrorResponse, useRouteError } from 'react-router-dom';
import { Compass, RotateCcw } from 'lucide-react';
import { Button, buttonClass } from '../../components/ui/primitives';

export const NotFoundPage: React.FC<{ what?: 'page' | 'project' }> = ({ what = 'page' }) => (
  <div className="h-full flex items-center justify-center p-6">
    <div className="text-center max-w-md">
      <div className="mx-auto w-12 h-12 rounded-full bg-elevated flex items-center justify-center text-fg-3 mb-4">
        <Compass aria-hidden className="w-6 h-6" />
      </div>
      <p className="font-mono text-xs text-fg-3">404</p>
      <h1 className="text-2xl font-semibold text-fg mt-1">{what === 'project' ? 'Project not found' : 'Page not found'}</h1>
      <p className="text-[13px] text-fg-2 mt-2 leading-relaxed">
        {what === 'project'
          ? 'This demo project may have been archived, or demo data was reset.'
          : 'The address does not match any screen in AgenEditor.'}
      </p>
      <div className="mt-5 flex gap-2 justify-center">
        <Link to="/home" className={buttonClass('primary')}>
          Return Home
        </Link>
        <Link to="/projects" className={buttonClass('secondary')}>
          Browse projects
        </Link>
      </div>
    </div>
  </div>
);

/** Router-level error boundary: "Something went wrong" recovery (spec §4.13). */
export const RouteErrorPage: React.FC = () => {
  const err = useRouteError();
  if (isRouteErrorResponse(err) && err.status === 404) return <NotFoundPage />;
  const message = err instanceof Error ? err.message : 'An unexpected error occurred.';
  return (
    <div role="alert" className="h-full min-h-screen flex items-center justify-center p-6 bg-app text-fg">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold">Something went wrong</h1>
        <p className="text-[13px] text-fg-2 mt-2">This screen hit an error. Your demo data is unaffected.</p>
        <pre className="mt-3 p-2 rounded-md bg-surface border border-line text-left text-xs text-danger whitespace-pre-wrap">{message}</pre>
        <div className="mt-4 flex gap-2 justify-center">
          <Button variant="primary" icon={<RotateCcw className="w-3.5 h-3.5" />} onClick={() => window.location.reload()}>
            Reload
          </Button>
          <a href="/home" className={buttonClass('secondary')}>
            Return Home
          </a>
        </div>
      </div>
    </div>
  );
};
