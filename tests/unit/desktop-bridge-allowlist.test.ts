import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('desktop bridge operation allowlist', () => {
  it('permits every operation used by the live UI', () => {
    const repository = ['liveEngineRepository.ts', 'productionRepository.ts']
      .map((file) => readFileSync(resolve('apps/desktop/src/services', file), 'utf8'))
      .join('\n');
    const shell = readFileSync(resolve('apps/desktop/src-tauri/src/main.rs'), 'utf8');
    const allowedBlock = shell.match(/const ALLOWED: &\[&str\] = &\[([\s\S]*?)\];/);
    expect(allowedBlock).not.toBeNull();

    const used = [...repository.matchAll(/desktop(?:<[^>]+>)?\('([a-z_]+)'/g)]
      .map((match) => match[1]);
    const allowed = new Set([...allowedBlock![1].matchAll(/"([a-z_]+)"/g)]
      .map((match) => match[1]));

    expect(used.length).toBeGreaterThan(0);
    expect(used.filter((operation) => !allowed.has(operation))).toEqual([]);
  });
});
