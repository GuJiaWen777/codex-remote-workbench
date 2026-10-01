"""One-shot POSIX worker. Trusted policy arrives from the local MCP configuration.

This is an accidental-misuse guard, not a sandbox against a malicious SSH account.
"""
import contextlib
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PATCH_BYTES = 60000
STATE_NAME = ".remote-workbench"


class GuardError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise GuardError(message)


def clean_relative(value):
    require(isinstance(value, str) and value, "Empty path")
    require(not any(ord(c) < 32 for c in value) and "\\" not in value, "Invalid path characters")
    parts = value.split("/")
    require(not value.startswith("/") and all(p not in ("", ".", "..") for p in parts), "Use relative paths without traversal")
    require(STATE_NAME not in parts, "Internal state paths are reserved")
    return value


def reject_symlinks(path):
    for item in [path, *path.parents]:
        require(not item.is_symlink(), "Symlink paths are not supported")


def workspace(policy, workdir):
    require(isinstance(workdir, str) and workdir.startswith("/"), "workdir must be absolute POSIX path")
    path = Path(workdir)
    require(".." not in path.parts and STATE_NAME not in path.parts, "Invalid workspace path")
    reject_symlinks(path)
    path = path.resolve(strict=True)
    require(path.is_dir(), "Workspace must exist")
    for raw in policy["roots"]:
        root = Path(raw)
        require(root.is_absolute() and root != Path("/") and ".." not in root.parts, "Invalid allowed root")
        reject_symlinks(root)
        root = root.resolve(strict=True)
        if path == root or root in path.parents:
            return path, root
    raise GuardError("Workspace is outside allowed roots")


def safe_target(workdir, name):
    clean_relative(name)
    target = workdir / name
    reject_symlinks(target)
    require(not target.exists() or target.is_file(), "Target must be a regular file")
    if target.exists():
        require(stat.S_ISREG(target.stat().st_mode), "Target must be regular")
        require(target.stat().st_nlink == 1, "Hard-linked files are not supported")
        require(target.stat().st_size <= MAX_FILE_BYTES, "File exceeds size limit")
    return target


def contents(path):
    if not path.exists():
        return None
    require(path.stat().st_size <= MAX_FILE_BYTES, "File exceeds size limit")
    value = path.read_bytes()
    require(len(value) <= MAX_FILE_BYTES, "File exceeds size limit")
    value.decode("utf-8")
    require(b"\x00" not in value, "Binary files are not supported")
    return value


def digest(value):
    return None if value is None else hashlib.sha256(value).hexdigest()


def patch_targets(patch):
    require(isinstance(patch, str) and len(patch.encode("utf-8")) <= MAX_PATCH_BYTES, "Patch exceeds byte limit")
    lines = patch.splitlines()
    require(lines and lines[0] == "*** Begin Patch" and lines[-1] == "*** End Patch", "Invalid patch envelope")
    targets = {}
    for line in lines[1:-1]:
        if not line.startswith("*** "):
            continue
        if line == "*** End of File":
            continue
        found = False
        for action in ("Add", "Update", "Delete"):
            prefix = f"*** {action} File: "
            if line.startswith(prefix):
                name = clean_relative(line[len(prefix):])
                require(name not in targets, "Duplicate patch target")
                targets[name] = action
                found = True
                break
        require(found, "Unsupported patch directive (moves are not supported)")
    require(0 < len(targets) <= 32, "Patch must address 1 to 32 files")
    return targets


def inspect(request, workdir):
    paths = request["paths"]
    require(isinstance(paths, list) and 0 < len(paths) <= 32, "Inspect 1 to 32 files")
    result = []
    total_bytes = 0
    for name in paths:
        value = contents(safe_target(workdir, name))
        total_bytes += len(value or b"")
        require(total_bytes <= MAX_FILE_BYTES, "Combined inspection exceeds 2 MiB")
        item = {"path": name, "sha256": digest(value), "exists": value is not None}
        if request.get("includeContent", True) and value is not None:
            item["content"] = value.decode("utf-8")
        result.append(item)
    return {"ok": True, "files": result}


@contextlib.contextmanager
def root_lock(root):
    # Remote POSIX hosts only; Windows is intentionally not supported by this worker.
    import fcntl
    state = root / STATE_NAME
    reject_symlinks(state)
    state.mkdir(mode=0o700, exist_ok=True)
    require(state.is_dir() and state.stat().st_uid == os.getuid(), "Unsafe state directory owner")
    require(state.stat().st_mode & 0o077 == 0, "State directory must have mode 0700")
    lock_path = state / "lock"
    reject_symlinks(lock_path)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        require(os.fstat(fd).st_nlink == 1, "Unsafe lock file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield state
    except BlockingIOError as error:
        raise GuardError("Workspace is busy; another workbench patch holds the lock") from error
    finally:
        os.close(fd)


def apply(request, workdir, root, policy):
    targets = patch_targets(request["patch"])
    expected = request.get("expectedHashes", {})
    require(set(expected) == set(targets), "expectedHashes must cover exactly every patch target")
    with root_lock(root) as state:
        original = {}
        modes = {}
        for name, action in targets.items():
            path = safe_target(workdir, name)
            value = contents(path)
            require(digest(value) == expected[name], f"HASH_CONFLICT: {name}")
            require((value is None) == (action == "Add"), f"Wrong operation for existing/missing file: {name}")
            original[name] = value
            modes[name] = stat.S_IMODE(path.stat().st_mode) if value is not None else 0o600
        job_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex
        job = state / job_id
        stage = job / "stage"
        backup = job / "backup"
        stage.mkdir(parents=True, mode=0o700)
        backup.mkdir(mode=0o700)
        for name, value in original.items():
            if value is not None:
                staged = stage / name
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(value)
        audit = {"jobId": job_id, "workdir": str(workdir), "status": "staging", "targets": targets,
                 "beforeHashes": {name: digest(value) for name, value in original.items()}, "dryRun": request.get("dryRun", True)}
        audit_path = job / "audit.json"

        def save_audit():
            audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
            audit_path.chmod(0o600)

        save_audit()
        changed = []
        created_dirs = []
        try:
            executable = Path(policy["codexPath"])
            require(executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK), "Codex executable unavailable")
            # The Codex parser writes only staged copies, not live target files.
            result = subprocess.run([str(executable), "--codex-run-as-apply-patch", request["patch"]],
                                    cwd=stage, capture_output=True, timeout=20)
            require(result.returncode == 0, "PATCH_REJECTED: " + result.stderr.decode("utf-8", "replace")[:4000])
            after = {name: contents(stage / name) for name in targets}
            for name, action in targets.items():
                require((after[name] is None) == (action == "Delete"), "Unexpected staged result")
            diffs = []
            for name in targets:
                diffs.extend(difflib.unified_diff((original[name] or b"").decode().splitlines(True),
                                                (after[name] or b"").decode().splitlines(True),
                                                fromfile="a/" + name, tofile="b/" + name))
            audit["afterHashes"] = {name: digest(value) for name, value in after.items()}
            preview = "".join(diffs)
            base = {"ok": True, "jobId": job_id, "auditPath": str(audit_path),
                    "files": [{"path": name, "action": targets[name], "before": digest(original[name]), "after": digest(after[name])} for name in targets],
                    "diff": preview[:200000], "diffTruncated": len(preview) > 200000}
            if request.get("dryRun", True):
                audit["status"] = "previewed"
                save_audit()
                return {**base, "status": "previewed", "targetFilesChanged": False}
            # Recheck after parsing; the lock serializes this tool, not outside writers.
            for name in targets:
                require(digest(contents(safe_target(workdir, name))) == expected[name], f"HASH_CONFLICT: {name}")
            for name, value in original.items():
                if value is not None:
                    saved = backup / name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    saved.write_bytes(value)
                    saved.chmod(0o600)
            audit["status"] = "committing"
            save_audit()
            for name, value in after.items():
                target = safe_target(workdir, name)
                if value is None:
                    target.unlink()
                else:
                    missing = []
                    parent = target.parent
                    while not parent.exists():
                        missing.append(parent)
                        parent = parent.parent
                    for directory in reversed(missing):
                        reject_symlinks(directory)
                        directory.mkdir(mode=0o700)
                        created_dirs.append(directory)
                    staged = stage / name
                    staged.chmod(modes[name])
                    os.replace(staged, target)
                changed.append(name)
            audit["status"] = "committed"
            save_audit()
            return {**base, "status": "committed", "backupPath": str(backup), "targetFilesChanged": True}
        except Exception as error:
            rollback_errors = []
            for name in reversed(changed):
                try:
                    target = safe_target(workdir, name)
                    if original[name] is None:
                        target.unlink(missing_ok=True)
                    else:
                        restore = job / ("restore-" + uuid.uuid4().hex)
                        restore.write_bytes(original[name])
                        restore.chmod(modes[name])
                        os.replace(restore, target)
                except Exception as rollback_error:
                    rollback_errors.append(f"{name}: {rollback_error}")
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            audit["status"] = "rollback_incomplete" if rollback_errors else "failed_restored" if changed else "rejected"
            audit["error"] = str(error)
            audit["rollbackErrors"] = rollback_errors
            save_audit()
            return {"ok": False, "status": audit["status"], "error": str(error), "auditPath": str(audit_path), "rollbackErrors": rollback_errors}


def handle(request):
    policy = request["policy"]
    workdir, root = workspace(policy, request["workdir"])
    op = request["op"]
    if op == "doctor":
        executable = Path(policy["codexPath"])
        available = executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK)
        return {"ok": available, "python": sys.version.split()[0], "workspaceExists": True,
                "workspaceWritable": os.access(workdir, os.W_OK), "codexExecutable": available,
                "note": "Executable presence does not prove patch-entry compatibility; run a preview smoke test."}
    if op == "inspect":
        return inspect(request, workdir)
    if op == "apply":
        return apply(request, workdir, root, policy)
    raise GuardError("Unknown operation")


def main():
    try:
        raw = sys.stdin.buffer.read(256 * 1024 + 1)
        require(len(raw) <= 256 * 1024, "Request too large")
        result = handle(json.loads(raw))
    except Exception as error:
        result = {"ok": False, "error": str(error)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
