import { readFile } from 'node:fs/promises';
import { z } from 'zod';

const posixPath = z.string().min(2).refine(
  value => value.startsWith('/') && !/[\x00-\x1f\\]/.test(value),
  'Must be an absolute POSIX path without control characters or backslashes',
);
export const configSchema = z.object({
  hosts: z.record(z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]*$/), z.object({
    sshAlias: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]*$/),
    roots: z.array(posixPath.refine(value => value !== '/', 'Filesystem root is forbidden')).min(1),
    codexPath: posixPath,
    pythonCommand: z.string().regex(/^(python3|\/[A-Za-z0-9_./-]+)$/).default('python3'),
    timeoutMs: z.number().int().min(5000).max(120000).default(45000),
  }).strict()).refine(hosts => Object.keys(hosts).length > 0, 'At least one host is required'),
}).strict();

export async function loadConfig(file) {
  if (!file) throw new Error('Provide --config /absolute/path/to/private-config.json');
  return configSchema.parse(JSON.parse(await readFile(file, 'utf8')));
}
