/**
 * Repository wiring. This is the ONE place that chooses the implementation.
 * Swap MockProjectRepository for a TauriProjectRepository during engine
 * integration; no screen imports the mock class directly.
 */
import { MockProjectRepository } from '../mocks/mockProjectRepository';
import type { ProjectRepository } from './projectRepository';

export function createDefaultRepository(): ProjectRepository & { persistsLocally: boolean } {
  return new MockProjectRepository({ latencyMs: 120 });
}
