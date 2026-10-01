#!/usr/bin/env node
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { loadConfig } from './config.js';
import { remoteCall } from './transport.js';

const args = process.argv.slice(2);
if (args.length !== 2 || args[0] !== '--config') {
  console.error('Usage: node src/server.js --config /absolute/path/to/private-config.json');
  process.exit(2);
}
const config = await loadConfig(args[1]);
const server = new McpServer({ name: 'codex-remote-workbench', version: '0.1.0' });
const common = { host: z.enum(Object.keys(config.hosts)), workdir: z.string().min(1) };
const relativePath = z.string().min(1).max(1024);
const hashes = z.record(relativePath, z.union([z.string().regex(/^[a-f0-9]{64}$/), z.null()]));
function register(name, description, inputSchema, op, writes = false) {
  server.registerTool(name, {
    description, inputSchema,
    annotations: { readOnlyHint: !writes, destructiveHint: writes, idempotentHint: !writes, openWorldHint: false },
  }, async input => {
    try {
      const result = await remoteCall(config.hosts[input.host], { ...input, op });
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }], isError: !result.ok };
    } catch (error) {
      return { content: [{ type: 'text', text: JSON.stringify({ ok: false, error: error.message }) }], isError: true };
    }
  });
}
register('remote_doctor', 'Read-only check of an allowed workspace and remote Codex executable. Does not install or change configuration.', common, 'doctor');
register('remote_inspect', 'Read up to 32 UTF-8 files within an allowed workspace and return SHA-256 hashes. Missing files return null hashes. Inspect every target before patching.',
  { ...common, paths: z.array(relativePath).min(1).max(32), includeContent: z.boolean().default(true) }, 'inspect');
register('remote_apply_patch', 'Apply a standard Codex patch to staged copies, then back up and replace target files. Requires inspected hashes for ALL targets (null for new files). dryRun previews without changing target files but creates staging/audit artifacts. No moves, symlinks or arbitrary commands. SSH timeout means outcome may be unknown: inspect before retrying.',
  { ...common, patch: z.string().min(1).max(60000), expectedHashes: hashes, dryRun: z.boolean().default(true) }, 'apply', true);
await server.connect(new StdioServerTransport());
