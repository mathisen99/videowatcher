import os
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "install with spaces"

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, *args, env=None):
        return subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--skip-deps", "--prefix", str(self.prefix), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_install_update_and_history_preservation(self):
        first = self.install("--source", str(ROOT))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Installation complete", first.stdout)
        self.assertIn("( o.o )", first.stdout)
        self.assertTrue((self.prefix / "bin/video-watcher").stat().st_mode & 0o111)
        self.assertTrue(
            (self.prefix / "config/autostart/io.github.mathisen99.VideoWatcher.desktop").is_file()
        )
        history = self.prefix / "share/videowatcher/history.sqlite3"
        db = sqlite3.connect(history)
        db.execute("CREATE TABLE marker(value TEXT)")
        db.execute("INSERT INTO marker VALUES ('keep my progress')")
        db.commit()
        db.close()
        second = self.install("--source", str(ROOT))
        self.assertEqual(second.returncode, 0, second.stderr)
        db = sqlite3.connect(history)
        self.assertEqual(db.execute("SELECT value FROM marker").fetchone()[0], "keep my progress")
        db.close()

    def test_no_autostart_and_invalid_argument(self):
        result = self.install("--source", str(ROOT), "--no-autostart")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.prefix / "config/autostart").exists())
        result = self.install("--unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Installation complete", result.stdout)

    def curl_stub(self, body):
        binaries = self.root / "bin"
        binaries.mkdir()
        curl = binaries / "curl"
        curl.write_text(body)
        curl.chmod(0o755)
        return {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"]}

    def test_download_failure_never_reports_success(self):
        env = self.curl_stub("#!/bin/sh\nexit 22\n")
        result = self.install(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Installation complete", result.stdout)
        self.assertFalse(self.prefix.exists())

    def test_downloaded_archive_installs(self):
        archive = self.root / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for name in ("install.py", "run.py", "videowatcher", "assets", "README.md", "docs"):
                output.add(
                    ROOT / name,
                    arcname="videowatcher-main/" + name,
                    filter=lambda entry: None if "__pycache__" in entry.name else entry,
                )
        env = self.curl_stub(
            "#!/usr/bin/python3\nimport os,shutil,sys\n"
            "shutil.copyfile(os.environ['VIDEOWATCHER_TEST_ARCHIVE'], sys.argv[sys.argv.index('--output') + 1])\n"
        )
        env["VIDEOWATCHER_TEST_ARCHIVE"] = str(archive)
        result = self.install(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.prefix / "share/videowatcher/app/videowatcher/app.py").is_file())

    def test_debian_arch_and_fedora_package_routes(self):
        functions = (ROOT / "install.sh").read_text().rsplit('\nmain "$@"', 1)[0]
        for distro, package in (
            ("debian", "vlc-plugin-base"),
            ("arch", "vlc-plugin-dbus"),
            ("fedora", "vlc-plugin-ffmpeg"),
        ):
            with self.subTest(distro=distro):
                # Stub the OS description and sudo; no package manager is executed.
                script = functions + (
                    f"\nsource() {{ ID={distro}; ID_LIKE=''; }}\n"
                    "sudo() { printf '%s\\n' \"$*\"; }\ninstall_dependencies\n"
                )
                result = subprocess.run(
                    ["bash", "-c", script], capture_output=True, text=True, timeout=10
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(package, result.stdout)


if __name__ == "__main__":
    unittest.main()
