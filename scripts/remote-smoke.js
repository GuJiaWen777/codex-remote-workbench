// Explicit opt-in integration test; private config must point to a NEW isolated test root.
// Never run against a production workspace. Only our UUID-named test file is edited/deleted.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { loadConfig } from '../src/config.js';
import { remoteCall } from '../src/transport.js';

const config = await loadConfig(process.argv[2]);
const host = process.argv[3];
const profile = config.hosts[host];
if (!profile) throw new Error('Choose an explicitly configured test host');
if (process.argv[4] !== '--isolated-test-root') throw new Error('Confirm a dedicated temporary root with --isolated-test-root');
const workdir = profile.roots[0];
const file = `workbench-smoke-${randomUUID()}.txt`;
const call = input => remoteCall(profile, { workdir, ...input });
const check = result => { assert.equal(result.ok, true, JSON.stringify(result)); return result; };
check(await call({ op: 'doctor' }));
let inspected = check(await call({ op: 'inspect', paths: [file] }));
assert.equal(inspected.files[0].sha256, null);
const add = `*** Begin Patch\n*** Add File: ${file}\n+hello\n*** End Patch`;
check(await call({ op: 'apply', patch: add, expectedHashes: { [file]: null }, dryRun: true }));
assert.equal(check(await call({ op: 'inspect', paths: [file] })).files[0].exists, false);
check(await call({ op: 'apply', patch: add, expectedHashes: { [file]: null }, dryRun: false }));
inspected = check(await call({ op: 'inspect', paths: [file] }));
assert.equal(inspected.files[0].content, 'hello\n');
const hash = inspected.files[0].sha256;
const update = `*** Begin Patch\n*** Update File: ${file}\n@@\n-hello\n+你好，remote workbench\n*** End Patch`;
const conflict = await call({ op: 'apply', patch: update, expectedHashes: { [file]: '0'.repeat(64) }, dryRun: false });
assert.equal(conflict.ok, false);
assert.match(conflict.error, /HASH_CONFLICT/);
check(await call({ op: 'apply', patch: update, expectedHashes: { [file]: hash }, dryRun: true }));
assert.equal(check(await call({ op: 'inspect', paths: [file] })).files[0].sha256, hash);
check(await call({ op: 'apply', patch: update, expectedHashes: { [file]: hash }, dryRun: false }));
inspected = check(await call({ op: 'inspect', paths: [file] }));
assert.equal(inspected.files[0].content, '你好，remote workbench\n');
const traversal = await call({ op: 'inspect', paths: ['../outside'] });
assert.equal(traversal.ok, false);
check(await call({ op: 'apply', patch: `*** Begin Patch\n*** Delete File: ${file}\n*** End Patch`,
  expectedHashes: { [file]: inspected.files[0].sha256 }, dryRun: false }));
assert.equal(check(await call({ op: 'inspect', paths: [file] })).files[0].exists, false);
// Do not print configured hosts, paths, content, hashes, diffs or audit records.
console.log('Remote smoke PASS: doctor, inspect, preview, add, update, delete, Unicode, conflict and traversal guards');
