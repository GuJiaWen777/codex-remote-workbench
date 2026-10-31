import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("worker", Path(__file__).resolve().parents[1] / "remote" / "worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root / "test.txt"
        self.file.write_bytes(b"old\n")
        self.policy = {"codexPath": __import__("sys").executable}
        self.lock = patch.object(worker, "root_lock", self.fake_lock)
        self.lock.start()
        self.addCleanup(self.lock.stop)
        self.addCleanup(self.temp.cleanup)

    @contextlib.contextmanager
    def fake_lock(self, root):
        state = root / worker.STATE_NAME
        state.mkdir(exist_ok=True)
        yield state

    def request(self, dry=True):
        return {"patch": "*** Begin Patch\n*** Update File: test.txt\n@@\n-old\n+new\n*** End Patch",
                "expectedHashes": {"test.txt": worker.digest(b"old\n")}, "dryRun": dry}

    def fake_parser(self, argv, cwd, **kwargs):
        (cwd / "test.txt").write_bytes(b"new\n")
        return subprocess.CompletedProcess(argv, 0, b"ok", b"")

    def test_paths_and_directives(self):
        for name in ["../outside", "/etc/passwd", "a//b", "./a", "a\\b", ".remote-workbench/a", "a\nb"]:
            with self.subTest(name=name), self.assertRaises(worker.GuardError):
                worker.clean_relative(name)
        for directive in ["*** Move to: b", "*** Begin Patch", "*** Unknown: b"]:
            with self.subTest(directive=directive), self.assertRaises(worker.GuardError):
                worker.patch_targets("*** Begin Patch\n*** Update File: a\n" + directive + "\n*** End Patch")

    def test_duplicate_and_size_limits(self):
        with self.assertRaises(worker.GuardError):
            worker.patch_targets("*** Begin Patch\n*** Add File: a\n+x\n*** Add File: a\n+y\n*** End Patch")
        with self.assertRaises(worker.GuardError):
            worker.patch_targets("*** Begin Patch\n*** Add File: a\n+" + "中" * 22000 + "\n*** End Patch")

    def test_inspect_missing_and_existing(self):
        result = worker.inspect({"paths": ["test.txt", "missing.txt"]}, self.root)
        self.assertEqual(result["files"][0]["sha256"], worker.digest(b"old\n"))
        self.assertIsNone(result["files"][1]["sha256"])

    def test_binary_rejected(self):
        self.file.write_bytes(b"a\x00b")
        with self.assertRaises(worker.GuardError):
            worker.inspect({"paths": ["test.txt"]}, self.root)

    def test_list_search_and_limits(self):
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / 'source.py').write_text('hello needle\n', encoding='utf-8')
        (self.root / '.env').write_text('needle secret', encoding='utf-8')
        (self.root / 'private.key').write_text('needle secret', encoding='utf-8')
        listing = worker.list_dir({'limit': 1}, self.root)
        self.assertEqual(len(listing['entries']), 1)
        self.assertTrue(listing['truncated'])
        result = worker.search({'query': 'needle'}, self.root)
        self.assertEqual([m['path'] for m in result['matches']], ['sub/source.py'])
        self.assertEqual(result['matches'][0]['line'], 1)
        self.assertFalse(result['truncated'])
        self.assertTrue(worker.search({'query': 'needle', 'limit': 1}, self.root)['truncated'])
        for path in ['../outside', '.remote-workbench', '.hidden']:
            with self.subTest(path=path), self.assertRaises(worker.GuardError):
                worker.list_dir({'path': path}, self.root)

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink test')
    def test_search_skips_symlink(self):
        (self.root / 'link').symlink_to(self.root.parent, target_is_directory=True)
        self.assertEqual(worker.search({'query': 'old'}, self.root)['matches'][0]['path'], 'test.txt')
        with self.assertRaises(worker.GuardError):
            worker.list_dir({'path': 'link'}, self.root)

    def test_hash_conflict_prevents_parser_call(self):
        request = self.request(False)
        request["expectedHashes"]["test.txt"] = "0" * 64
        with patch.object(worker.subprocess, "run") as parser, self.assertRaises(worker.GuardError):
            worker.apply(request, self.root, self.root, self.policy)
        parser.assert_not_called()
        self.assertEqual(self.file.read_bytes(), b"old\n")

    def test_exact_expected_target_set(self):
        request = self.request()
        request["expectedHashes"] = {}
        with self.assertRaises(worker.GuardError):
            worker.apply(request, self.root, self.root, self.policy)

    def test_preview_does_not_change_target(self):
        with patch.object(worker.subprocess, "run", self.fake_parser):
            result = worker.apply(self.request(), self.root, self.root, self.policy)
        self.assertEqual(result["status"], "previewed")
        self.assertIn("+new", result["diff"])
        self.assertEqual(self.file.read_bytes(), b"old\n")

    def test_apply_backups_and_audit(self):
        with patch.object(worker.subprocess, "run", self.fake_parser):
            result = worker.apply(self.request(False), self.root, self.root, self.policy)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.file.read_bytes(), b"new\n")
        self.assertEqual((Path(result["backupPath"]) / "test.txt").read_bytes(), b"old\n")
        self.assertEqual(json.loads(Path(result["auditPath"]).read_text())["status"], "committed")

    def test_parser_failure_cannot_change_live_file(self):
        with patch.object(worker.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"bad hunk")):
            result = worker.apply(self.request(False), self.root, self.root, self.policy)
        self.assertFalse(result["ok"])
        self.assertEqual(self.file.read_bytes(), b"old\n")

    def test_external_change_during_staging_is_detected(self):
        def parser(*args, **kwargs):
            self.file.write_bytes(b"external\n")
            return self.fake_parser(*args, **kwargs)
        with patch.object(worker.subprocess, "run", parser):
            result = worker.apply(self.request(False), self.root, self.root, self.policy)
        self.assertFalse(result["ok"])
        self.assertIn("HASH_CONFLICT", result["error"])
        self.assertEqual(self.file.read_bytes(), b"external\n")

    def test_rollback_after_second_write_fails(self):
        second = self.root / "second.txt"
        second.write_bytes(b"old2\n")
        request = self.request(False)
        request["patch"] = request["patch"].replace("*** End Patch", "*** Update File: second.txt\n@@\n-old2\n+new2\n*** End Patch")
        request["expectedHashes"]["second.txt"] = worker.digest(b"old2\n")
        def parser(argv, cwd, **kwargs):
            self.fake_parser(argv, cwd, **kwargs)
            (cwd / "second.txt").write_bytes(b"new2\n")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        real_replace = os.replace
        def replace(source, target):
            if Path(target) == second:
                raise OSError("simulated write failure")
            return real_replace(source, target)
        with patch.object(worker.subprocess, "run", parser), patch.object(worker.os, "replace", replace):
            result = worker.apply(request, self.root, self.root, self.policy)
        self.assertEqual(result["status"], "failed_restored")
        self.assertEqual(self.file.read_bytes(), b"old\n")
        self.assertEqual(second.read_bytes(), b"old2\n")

    @unittest.skipIf(os.name == "nt", "POSIX symlink test")
    def test_symlink_is_rejected(self):
        (self.root / "alias").symlink_to(self.file)
        with self.assertRaises(worker.GuardError):
            worker.safe_target(self.root, "alias")

    @unittest.skipIf(os.name == "nt", "POSIX hard-link test")
    def test_hardlink_is_rejected(self):
        os.link(self.file, self.root / "alias")
        with self.assertRaises(worker.GuardError):
            worker.safe_target(self.root, "test.txt")

    @unittest.skipIf(os.name == "nt", "POSIX workspace test")
    def test_workspace_boundary(self):
        policy = {"roots": [str(self.root)]}
        self.assertEqual(worker.workspace(policy, str(self.root))[0], self.root.resolve())
        with self.assertRaises(worker.GuardError):
            worker.workspace(policy, "/")
        with self.assertRaises(worker.GuardError):
            worker.workspace(policy, str(self.root / ".."))

    @unittest.skipIf(os.name == "nt", "POSIX lock test")
    def test_real_lock_exclusion_and_permissions(self):
        self.lock.stop()
        with worker.root_lock(self.root) as state:
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(worker.GuardError):
                with worker.root_lock(self.root):
                    pass


if __name__ == "__main__":
    unittest.main()
