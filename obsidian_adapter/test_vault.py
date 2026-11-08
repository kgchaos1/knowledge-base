"""Offline tests; run python -m unittest obsidian_adapter.test_vault."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from . import vault
from .render import stable_path


def note(note_id=1, content="server", revision=1):
    return {"id": note_id, "title": "test", "content": content, "revision": revision}


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pkm-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "dedicated-pkm-export"

    def apply(self, notes=None, attachments=None, snapshot="s1"):
        return vault.apply_snapshot(self.root, [note()] if notes is None else notes,
                                    attachments or {}, snapshot)

    def target(self, note_id=1):
        return self.root / stable_path(note(note_id))

    def index(self):
        return json.loads((self.root / vault._MARKER).read_text(encoding="utf-8"))

    def test_empty_root_import_update_unchanged(self):
        self.root.mkdir()
        self.assertEqual(self.apply()["imported"], ["1"])
        self.assertEqual(self.apply()["unchanged"], ["1"])
        self.assertEqual(self.apply([note(content="new", revision=2)])["updated"], ["1"])
        self.assertEqual(self.index()["notes"]["1"]["revision"], 2)

    def test_nonempty_without_marker_is_rejected(self):
        self.root.mkdir()
        (self.root / "personal.md").write_text("mine")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse((self.root / vault._MARKER).exists())

    def test_local_edit_conflicts_do_not_promote_baseline(self):
        self.apply()
        baseline = self.index()["notes"]["1"]["hash"]
        self.target().write_text("local change", encoding="utf-8")
        incoming = [note(content="new server", revision=2)]
        self.assertEqual(self.apply(incoming)["conflicts"], ["1"])
        self.assertEqual(self.target().read_text(encoding="utf-8"), "local change")
        self.assertEqual(self.index()["notes"]["1"]["hash"], baseline)
        self.assertEqual(self.apply(incoming)["conflicts"], ["1"])
        self.assertEqual(len(list((self.root / "冲突").iterdir())), 1)

    def test_same_server_keeps_local_edit(self):
        self.apply()
        self.target().write_text("mine", encoding="utf-8")
        self.assertEqual(self.apply()["retained"], ["1"])
        self.assertFalse((self.root / "冲突").exists())
        self.assertEqual(self.target().read_text(), "mine")

    def test_deleted_remote_note_retained(self):
        self.apply()
        self.assertEqual(self.apply([])["retained"], ["1"])
        self.assertTrue(self.target().exists())
        self.assertTrue(self.index()["notes"]["1"]["retained"])

    def test_failed_update_retry_reclaims_generated_bytes(self):
        self.apply()
        previous = (self.root / vault._MARKER).read_bytes()
        actual = vault._atomic
        def fail_index(root, relative, data, **kwargs):
            if relative == vault._MARKER:
                raise OSError("simulated index failure")
            return actual(root, relative, data, **kwargs)
        incoming = [note(content="new server", revision=2)]
        with patch.object(vault, "_atomic", side_effect=fail_index):
            with self.assertRaises(OSError):
                self.apply(incoming, snapshot="s2")
        self.assertEqual((self.root / vault._MARKER).read_bytes(), previous)
        self.assertEqual(self.apply(incoming, snapshot="s2")["unchanged"], ["1"])
        self.assertEqual(self.index()["snapshot"], "s2")
        self.assertFalse((self.root / "冲突").exists())

    def test_first_run_failure_retry(self):
        actual = vault._atomic
        def fail_second(root, relative, data, **kwargs):
            if relative == "笔记/pkm-2.md":
                raise OSError("simulated file failure")
            return actual(root, relative, data, **kwargs)
        incoming = [note(), note(2)]
        with patch.object(vault, "_atomic", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.apply(incoming)
        self.assertIsNone(self.index()["snapshot"])
        result = self.apply(incoming)
        self.assertEqual(result["unchanged"], ["1"])
        self.assertEqual(result["imported"], ["2"])

    def test_all_rendering_precedes_writes(self):
        with self.assertRaises(ValueError):
            self.apply([note(), note("../unsafe")])
        self.assertFalse(self.root.exists())

    def test_unsafe_generated_path_and_attachment_magic(self):
        with patch.object(vault, "stable_path", return_value="../escape.md"):
            with self.assertRaises(ValueError):
                self.apply()
        image = b"not an image"
        name = hashlib.sha256(image).hexdigest() + ".png"
        with self.assertRaises(ValueError):
            self.apply(attachments={name: image})
        self.assertFalse(self.root.exists())

    def test_marker_rejects_extra_fields_and_unsafe_paths(self):
        self.apply()
        marker = self.root / vault._MARKER
        index = self.index()
        index["credentials"] = "must never persist"
        marker.write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.apply()
        del index["credentials"]
        index["notes"]["1"]["path"] = "../outside.md"
        marker.write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.apply()

    def test_attachment_validation_and_existing_hash(self):
        image = b"\x89PNG\r\n\x1a\nimage"
        name = hashlib.sha256(image).hexdigest() + ".png"
        self.apply(attachments={name: image})
        self.assertEqual((self.root / "附件" / name).read_bytes(), image)
        with self.assertRaises(ValueError):
            self.apply(attachments={"../" + name: image})
        with self.assertRaises(ValueError):
            self.apply(attachments={name: b"bad"})
        (self.root / "附件" / name).write_bytes(b"tamper")
        with self.assertRaises(ValueError):
            self.apply(attachments={name: image})
        self.assertEqual((self.root / "附件" / name).read_bytes(), b"tamper")

    def test_symlink_rejected(self):
        self.apply()
        external = Path(self.temp.name) / "outside.md"
        external.write_text("outside")
        self.target().unlink()
        try:
            self.target().symlink_to(external)
        except OSError as exc:
            self.skipTest("Platform does not permit symlinks: " + str(exc))
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(external.read_text(), "outside")

    def test_conflict_copy_no_clobber(self):
        self.apply()
        self.target().write_text("mine")
        incoming = [note(content="new")]
        self.apply(incoming)
        conflict = next((self.root / "冲突").iterdir())
        conflict.write_text("edited conflict")
        previous = (self.root / vault._MARKER).read_bytes()
        with self.assertRaises(ValueError):
            self.apply(incoming, snapshot="s2")
        self.assertEqual(conflict.read_text(), "edited conflict")
        self.assertEqual((self.root / vault._MARKER).read_bytes(), previous)

    def test_zip_allowlist_and_no_credentials(self):
        self.apply([{**note(), "token": "SECRET", "password": "SECRET"}])
        hidden = self.root / ".obsidian" / "plugins"
        hidden.mkdir(parents=True)
        (hidden / "unsafe.js").write_text("SECRET")
        (self.root / "private.txt").write_text("SECRET")
        with zipfile.ZipFile(io.BytesIO(vault.build_zip(self.root))) as archive:
            self.assertEqual(set(archive.namelist()), {vault._MARKER, "说明.md", "笔记/pkm-1.md"})
            for filename in archive.namelist():
                self.assertNotIn(b"SECRET", archive.read(filename))
        self.assertEqual(set(self.index()), {"notes", "snapshot"})


if __name__ == "__main__":
    unittest.main()
