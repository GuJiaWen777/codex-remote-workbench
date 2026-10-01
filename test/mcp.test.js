import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

test('MCP initialization, tools/list and schema error without contacting any real server', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'remote-workbench-mcp-'));
  const config = join(dir, 'config.json');
  await writeFile(config, JSON.stringify({ hosts: { development: {
    sshAlias: 'example-host', roots: ['/srv/example/workspace'], codexPath: '/usr/bin/codex',
  } } }));
  const transport = new StdioClientTransport({ command: process.execPath,
    args: [fileURLToPath(new URL('../src/server.js', import.meta.url)), '--config', config], stderr: 'pipe' });
  const client = new Client({ name: 'smoke-test', version: '1.0.0' });
  try {
    await client.connect(transport);
    const result = await client.listTools();
    assert.deepEqual(result.tools.map(tool => tool.name).sort(), ['remote_apply_patch', 'remote_doctor', 'remote_inspect']);
    assert.equal(result.tools.find(tool => tool.name === 'remote_apply_patch').annotations.destructiveHint, true);
    const invalid = await client.callTool({ name: 'remote_doctor', arguments: { host: 'unconfigured', workdir: '/' } });
    assert.equal(invalid.isError, true);
  } finally {
    await client.close();
    await rm(dir, { recursive: true, force: true });
  }
});
