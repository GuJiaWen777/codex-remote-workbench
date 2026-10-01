import test from 'node:test';
import assert from 'node:assert/strict';
import { configSchema } from '../src/config.js';
import { sshInvocation, remoteCall } from '../src/transport.js';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';

const profile = { sshAlias: 'example-host', roots: ['/srv/example/workspace'], codexPath: '/usr/bin/codex', pythonCommand: 'python3', timeoutMs: 5000 };
test('configuration supplies defaults and rejects shell injection, unknown keys and filesystem root', () => {
  const config = configSchema.parse({ hosts: { development: profile } });
  assert.equal(config.hosts.development.timeoutMs, 5000);
  for (const override of [{ sshAlias: '-oProxyCommand=bad' }, { sshAlias: 'a;echo bad' },
    { roots: ['/'] }, { pythonCommand: 'python3; bad' }, { password: 'secret' }]) {
    assert.throws(() => configSchema.parse({ hosts: { development: { ...profile, ...override } } }));
  }
});
test('OpenSSH uses known-host verification and a fixed argv command', () => {
  const invocation = sshInvocation(profile);
  assert.equal(invocation.command, 'ssh');
  assert.ok(invocation.args.includes('StrictHostKeyChecking=yes'));
  assert.ok(invocation.args.includes('BatchMode=yes'));
  assert.ok(invocation.args.includes('--'));
  assert.equal(invocation.args.at(-2), 'example-host');
});
function fakeProcess(callback) {
  return (...args) => {
    const child = new EventEmitter();
    child.stdin = new PassThrough();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => { child.killed = true; };
    queueMicrotask(() => callback(child, args));
    return child;
  };
}
test('patch input goes to stdin and cannot alter the SSH command; trusted policy overrides input', async () => {
  const input = { patch: "'; echo INJECTION; '", policy: { roots: ['/'] } };
  const result = await remoteCall(profile, input, fakeProcess((child, args) => {
    assert.equal(args[2].shell, false);
    assert.ok(!args[1].at(-1).includes('INJECTION'));
    const sent = JSON.parse(child.stdin.read().toString());
    assert.deepEqual(sent.policy.roots, profile.roots);
    child.stdout.write('{"ok":true}');
    child.emit('close', 0);
  }));
  assert.equal(result.ok, true);
});
test('transport surfaces non-zero SSH exits', async () => {
  await assert.rejects(remoteCall(profile, {}, fakeProcess(child => {
    child.stderr.write('Host key verification failed');
    child.emit('close', 255);
  })), /SSH_EXIT_255/);
});
test('transport rejects malformed responses', async () => {
  await assert.rejects(remoteCall(profile, {}, fakeProcess(child => {
    child.stdout.write('login banner');
    child.emit('close', 0);
  })), /INVALID_RESPONSE/);
});
test('transport timeout is explicit about unknown outcomes and never retries', async () => {
  let launches = 0;
  const spawn = fakeProcess(() => { launches++; });
  await assert.rejects(remoteCall({ ...profile, timeoutMs: 10 }, {}, spawn), /outcome may be unknown/);
  assert.equal(launches, 1);
});
