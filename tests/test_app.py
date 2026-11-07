"""Quick checks run by GitHub Actions before a release is built, and by
`python -m unittest discover tests` locally. No network and no real mounts."""

import hashlib
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kamen import config, mounts, ssh, update, winutil  # noqa: E402


class Imports(unittest.TestCase):
    def test_ui_imports(self):
        from kamen import ui
        self.assertTrue(hasattr(ui, "App"))

    def test_version_matches_file(self):
        data = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(config.APP_VERSION, data["version"])
        self.assertEqual(len(config._version_tuple(data["version"])), 3)

    def test_version_compare(self):
        self.assertGreater(config._version_tuple("1.10.0"), config._version_tuple("1.9.9"))
        self.assertGreater(config._version_tuple("v2.0.0"), config._version_tuple("1.9.9"))


class JumpHost(unittest.TestCase):
    def test_parse(self):
        p = ssh.parse_jump
        self.assertIsNone(p("", "me"))
        self.assertEqual(p("jump", "me"), ("me", "jump", 22))
        self.assertEqual(p("bob@jump:2222", "me"), ("bob", "jump", 2222))
        self.assertEqual(p("fe80::1%12", "me"), ("me", "fe80::1%12", 22))
        self.assertEqual(p("a@[2001:db8::5]:2200", "me"), ("a", "2001:db8::5", 2200))
        for bad in ("host:x", "host:70000", "two words", "[::1]x"):
            with self.assertRaises(ValueError):
                p(bad, "me")

    def test_hop_args(self):
        conn = {"user": "me", "jump_host": "bob@jump:2222", "auth_type": "key",
                "key_path": r"C:\keys\id", "verify_host_key": True}
        args = ssh.jump_hop_args(conn, "ssh")
        self.assertEqual(args[:3], ["ssh", "-p", "2222"])
        self.assertIn("C:/keys/id", args)
        self.assertNotIn("StrictHostKeyChecking=no", args)
        self.assertEqual(args[-3:], ["-W", "[%h]:%p", "bob@jump"])


class Tunnels(unittest.TestCase):
    conn = {"user": "me", "host": "server", "port": 22, "auth_type": "password"}

    def args(self, t, **conn):
        with mock.patch.object(ssh, "_find_ssh_client", return_value="ssh.exe"):
            return ssh._tunnel_ssh_args(dict(self.conn, **conn), t)

    def test_local(self):
        t = {"type": "local", "local_port": 8080, "remote_host": "::1", "remote_port": 80}
        self.assertIn("127.0.0.1:8080:[::1]:80", self.args(t))

    def test_socks(self):
        self.assertIn("127.0.0.1:1080", self.args({"type": "socks", "local_port": 1080}))

    def test_remote(self):
        t = {"type": "remote", "remote_port": 8081, "local_host": "127.0.0.1", "local_port": 3000}
        args = self.args(t)
        self.assertEqual(args[args.index("-R") + 1], "8081:127.0.0.1:3000")
        self.assertIn("Server port 8081", ssh.describe_tunnel(t))

    def test_through_jump_host(self):
        args = self.args({"type": "socks", "local_port": 1080}, jump_host="jump")
        self.assertTrue(any(a.startswith("ProxyCommand=") for a in args))
        self.assertEqual(args[-1], "me@server")


class MountOptions(unittest.TestCase):
    def test_defaults_keep_old_behaviour(self):
        self.assertTrue(mounts.mount_option({}, "follow_symlinks"))
        for key in ("compression", "hide_dot_files", "no_dir_cache", "rename_fix"):
            self.assertFalse(mounts.mount_option({}, key))

    def test_saved_choice_wins(self):
        conn = {"mount_options": {"follow_symlinks": False, "compression": True}}
        self.assertFalse(mounts.mount_option(conn, "follow_symlinks"))
        self.assertTrue(mounts.mount_option(conn, "compression"))


class Helpers(unittest.TestCase):
    def test_io_total(self):
        import os
        first = winutil.io_total([os.getpid()])
        self.assertIsInstance(first, int)
        (ROOT / "version.json").read_bytes()  # some I/O
        self.assertGreaterEqual(winutil.io_total([os.getpid()]), first)
        self.assertIsNone(winutil.io_total([]))

    def test_human_size(self):
        self.assertEqual(winutil.human_size(512), "512 bytes")
        self.assertEqual(winutil.human_size(1536), "1.5 KB")


class Update(unittest.TestCase):
    """The parts of Update Now that decide what gets installed or deleted."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.exe = self.dir / "Kamen.SSHFS.Manager.exe"
        self.exe.write_bytes(b"old")
        patcher = mock.patch.object(sys, "executable", str(self.exe))
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, files):
        """Local web server with the given {name: bytes}; returns the base URL."""
        root = self.dir / "srv"
        root.mkdir()
        for name, data in files.items():
            (root / name).write_bytes(data)
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(root), **k)
        http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}/"

    def info(self, exe_data, sums_hash):
        base = self.serve({update.EXE_ASSET: exe_data,
                           "SHA256SUMS.txt": f"{sums_hash}  {update.EXE_ASSET}\n".encode()})
        return {"exe": base + update.EXE_ASSET, "sums": base + "SHA256SUMS.txt"}

    def test_good_download_is_kept(self):
        data = b"new exe"
        new = update.download(self.info(data, hashlib.sha256(data).hexdigest()))
        self.assertEqual(new.read_bytes(), data)

    def test_bad_checksum_is_refused_and_removed(self):
        with self.assertRaises(RuntimeError):
            update.download(self.info(b"tampered", "0" * 64))
        self.assertFalse((self.dir / (self.exe.name + ".new")).exists())

    def test_backup_names_never_overwrite(self):
        self.assertEqual(update.old_path().name, "Kamen.SSHFS.Manager.exe.old")
        update.old_path().write_bytes(b"backup")
        self.assertEqual(update.old_path().name, "Kamen.SSHFS.Manager2.exe.old")

    def test_swap(self):
        new = self.dir / "new.exe"
        new.write_bytes(b"new")
        old = update.old_path()
        update.swap_in(new, old)
        self.assertEqual(self.exe.read_bytes(), b"new")
        self.assertEqual(old.read_bytes(), b"old")

    def cleanup(self, argv):
        old = update.old_path()
        old.write_bytes(b"old")
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "argv", ["app"] + argv), \
             mock.patch.object(update.threading, "Thread") as thread:
            update.cleanup_old()
            for call in thread.call_args_list:  # run the worker right here
                call.kwargs["target"]()
        return old.exists()

    def test_cleanup_only_right_after_an_update(self):
        self.assertTrue(self.cleanup([]))
        self.assertTrue(self.cleanup(["--startup"]))
        self.assertTrue(self.cleanup(["--updated", "--keep-old"]))

    def test_cleanup_removes_only_its_own_old_copy(self):
        update.old_path().write_bytes(b"kept backup")
        target = self.dir / "Kamen.SSHFS.Manager2.exe.old"
        self.assertFalse(self.cleanup(["--updated", "--remove-old=" + str(target)]))
        self.assertTrue((self.dir / "Kamen.SSHFS.Manager.exe.old").exists())

    def test_cleanup_ignores_files_elsewhere(self):
        outside = Path(tempfile.mkdtemp()) / "x.old"
        outside.write_bytes(b"x")
        self.cleanup(["--updated", "--remove-old=" + str(outside)])
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
