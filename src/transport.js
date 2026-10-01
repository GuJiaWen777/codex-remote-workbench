import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';

const worker = await readFile(new URL('../remote/worker.py', import.meta.url), 'utf8');
const encodedWorker = Buffer.from(worker).toString('base64');

export function sshInvocation(profile) {
  // Only trusted configuration reaches the remote command. Tool input travels on stdin.
  const program = `import base64;exec(compile(base64.b64decode('${encodedWorker}'),'<remote-workbench>','exec'))`;
  const quoted = "'" + program.replaceAll("'", "'\\''") + "'";
  return {
    command: 'ssh',
    args: ['-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
      '-o', 'ConnectTimeout=10', '--', profile.sshAlias, `${profile.pythonCommand} -c ${quoted}`],
  };
}

export function remoteCall(profile, input, spawnProcess = spawn) {
  const { command, args } = sshInvocation(profile);
  return new Promise((resolve, reject) => {
    const child = spawnProcess(command, args, { shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    let settled = false;
    let stdout = [];
    let stderr = [];
    let size = 0;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error); else resolve(value);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish(new Error('SSH_TIMEOUT: remote outcome may be unknown; inspect files and audit before retrying'));
    }, profile.timeoutMs);
    for (const [stream, chunks] of [[child.stdout, stdout], [child.stderr, stderr]]) {
      stream.on('data', chunk => {
        size += chunk.length;
        if (size > 8 * 1024 * 1024) {
          child.kill();
          finish(new Error('OUTPUT_LIMIT: inspect remote audit; do not retry blindly'));
        } else chunks.push(chunk);
      });
    }
    child.on('error', error => finish(error));
    child.stdin.on('error', error => finish(error));
    child.on('close', code => {
      if (settled) return;
      if (code !== 0) return finish(new Error(`SSH_EXIT_${code}: ${Buffer.concat(stderr).toString('utf8').slice(0, 4096)}`));
      try { finish(null, JSON.parse(Buffer.concat(stdout).toString('utf8'))); }
      catch { finish(new Error('INVALID_RESPONSE: remote worker did not return JSON; inspect SSH startup output')); }
    });
    child.stdin.end(JSON.stringify({ ...input, policy: profile }));
  });
}
