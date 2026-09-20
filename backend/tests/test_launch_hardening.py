"""Launch-hardening unit tests (no display / network)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LocalTokenTests(unittest.TestCase):
    def test_verify_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "jarvis-api-token"
            with patch.dict("os.environ", {"ADA_API_TOKEN": "", "ADA_API_TOKEN_PATH": str(path)}, clear=False):
                import auth.local_token as lt

                lt._TOKEN = None
                token = lt.get_or_create_token()
                self.assertTrue(lt.verify_token(token))
                self.assertFalse(lt.verify_token("nope"))
                self.assertFalse(lt.verify_token(""))
                again = lt.get_or_create_token()
                self.assertEqual(token, again)
                lt._TOKEN = None
                self.assertFalse(lt.request_has_valid_token(type("R", (), {"headers": {}, "query_params": {"token": token}})()))
                self.assertTrue(
                    lt.request_has_valid_token(
                        type("R", (), {"headers": {}, "query_params": {"token": token}})(),
                        allow_query=True,
                    )
                )
                lt._TOKEN = None


class SecretBoxTests(unittest.TestCase):
    def test_software_seal_open(self):
        from auth.secret_box import open_json, seal_json

        payload = {"users": {"x": {"tokens": {"access_token": "secret-token"}}}}
        boxed = seal_json(payload)
        self.assertIn("blob", boxed)
        opened = open_json(boxed)
        self.assertEqual(opened, payload)

    def test_tamper_fails(self):
        from auth.secret_box import open_json, seal_json

        boxed = seal_json({"a": 1})
        blob = boxed["blob"]
        boxed["blob"] = ("A" if blob[0] != "A" else "B") + blob[1:]
        self.assertIsNone(open_json(boxed))


class DpiMappingTests(unittest.TestCase):
    def test_125_percent_windows(self):
        from agents.computer_use.perception import dpi_mouse_mapping

        sx, sy, ox, oy = dpi_mouse_mapping(1920, 1080, 0, 0, 1536, 864)
        self.assertAlmostEqual(sx, 1536 / 1920, places=4)
        self.assertAlmostEqual(sy, 864 / 1080, places=4)
        self.assertEqual(ox, 0)
        self.assertEqual(oy, 0)

    def test_near_identity_snaps(self):
        from agents.computer_use.perception import dpi_mouse_mapping

        sx, sy, ox, oy = dpi_mouse_mapping(1920, 1080, 100, 40, 1920, 1080)
        self.assertEqual(sx, 1.0)
        self.assertEqual(sy, 1.0)
        self.assertEqual(ox, 100)
        self.assertEqual(oy, 40)


class StartRunCancelTests(unittest.TestCase):
    def test_reuse_does_not_clear_cancel(self):
        from agents.run_control import cancel_run, is_cancelled, start_run

        rec = start_run(chat_id="c")
        rid = rec["run_id"]
        cancel_run(rid, "user_stop")
        start_run(chat_id="c", run_id=rid)
        self.assertTrue(is_cancelled(rid))


class RateLimitTests(unittest.TestCase):
    def test_bucket(self):
        from observability.rate_limit import allow

        key = "test.bucket"
        self.assertTrue(allow(key, limit=2, window_sec=30))
        self.assertTrue(allow(key, limit=2, window_sec=30))
        self.assertFalse(allow(key, limit=2, window_sec=30))


class ResumeRunIdTests(unittest.TestCase):
    def test_prefers_http_run_id(self):
        from agents.agent_state import resume_run

        task = {"id": "t1", "run_id": "old-run", "goal": "x", "plan": [], "chat_id": ""}
        st = resume_run("", task, preferred_run_id="http-run")
        self.assertEqual(st["run_id"], "http-run")


class StopReasonTests(unittest.TestCase):
    def test_cancel_and_spend(self):
        from agents.run_control import cancel_run, start_run, stop_reason

        rec = start_run(chat_id="t")
        rid = rec["run_id"]
        self.assertIsNone(stop_reason(rid))
        cancel_run(rid, "user_stop")
        self.assertEqual(stop_reason(rid), "Stopped by user.")

    def test_cancel_does_not_overwrite_complete(self):
        from agents.run_control import cancel_run, finish_run, get_run, start_run

        rec = start_run(chat_id="done")
        rid = rec["run_id"]
        finish_run(rid, "complete")
        cancel_run(rid, "stale_stop")
        self.assertEqual(get_run(rid)["status"], "complete")

    def test_writeback_skips_cancelled(self):
        from agents.run_control import cancel_run, set_current_ids, start_run
        from memory.writeback import schedule_write_back

        rec = start_run(chat_id="wb")
        set_current_ids(run_id=rec["run_id"], chat_id="wb")
        cancel_run(rec["run_id"], "user_stop")
        # Must not raise; cancelled runs should be skippable by callers.
        schedule_write_back(chat_id="wb", user_message="I prefer dark mode always", assistant_reply="")


class OauthStoreEncryptTests(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "google-oauth-store.json"
            with patch.dict("os.environ", {"GOOGLE_OAUTH_STORE_PATH": str(store)}, clear=False):
                from auth import google_oauth as go

                data = go._empty_store()
                data["users"]["sub1"] = {"tokens": {"access_token": "tok", "refresh_token": "rt"}}
                go._save_store(data)
                raw = json.loads(store.read_text(encoding="utf-8"))
                self.assertIn("blob", raw)
                self.assertNotIn("tok", store.read_text(encoding="utf-8"))
                loaded = go._load_store()
                self.assertEqual(loaded["users"]["sub1"]["tokens"]["access_token"], "tok")


class ReactionTests(unittest.TestCase):
    def test_up_down_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("memory.reactions.data_root", return_value=Path(td)), patch(
                "memory.facts.data_root", return_value=Path(td)
            ):
                from memory.reactions import add_reaction, format_recent_for_prompt, votes_for_chat

                self.assertTrue(add_reaction("c1", "up", "hello world")["ok"])
                self.assertTrue(add_reaction("c1", "down", "bad answer")["ok"])
                votes = votes_for_chat("c1")
                self.assertEqual(votes.get("hello world"), "up")
                self.assertEqual(votes.get("bad answer"), "down")
                self.assertIn("unhelpful", format_recent_for_prompt())


class HardwareSnapshotTests(unittest.TestCase):
    def test_snapshot_does_not_spawn_probe(self):
        import agents.hardware as hw

        with patch.object(hw, "schedule_hardware_probe") as sched, patch.object(
            hw, "_read_disk_cache", return_value=None
        ), patch.object(hw, "_run") as run, patch.object(hw, "_ps") as ps:
            hw._CACHE = None
            hw._CACHE_AT = 0.0
            snap = hw.hardware_snapshot(refresh=True)
            self.assertIsInstance(snap, dict)
            sched.assert_called_once()
            run.assert_not_called()
            ps.assert_not_called()
            hw._CACHE = None


class HiddenSubprocessTests(unittest.TestCase):
    def test_hidden_kwargs_on_windows(self):
        from tools.win_subprocess import hidden_popen_kwargs

        kwargs = hidden_popen_kwargs()
        if __import__("sys").platform == "win32":
            self.assertIn("creationflags", kwargs)
            self.assertIn("startupinfo", kwargs)
        else:
            self.assertEqual(kwargs, {})


class WorkspaceTreeListTests(unittest.TestCase):
    def test_list_tree_paths_includes_hidden_and_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".agents").mkdir()
            (root / ".agents" / "skill.md").write_text("ok", encoding="utf-8")
            (root / ".env").write_text("SECRET=1", encoding="utf-8")
            (root / "readme.md").write_text("hi", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "pkg.js").write_text("x", encoding="utf-8")
            (root / "empty").mkdir()
            with patch("tools.workspace_io.get_workspace_root", return_value=str(root)):
                from tools.workspace_io import list_rel_paths, list_tree_paths

                hidden = list_rel_paths()
                self.assertIn("readme.md", hidden)
                self.assertNotIn(".env", hidden)
                tree = list_tree_paths()
                self.assertIn(".env", tree)
                self.assertIn(".agents/", tree)
                self.assertIn(".agents/skill.md", tree)
                self.assertIn("empty/", tree)
                self.assertIn("readme.md", tree)
                self.assertFalse(any(p.startswith("node_modules") for p in tree))

    def test_tree_stamp_changes_when_file_added(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("1", encoding="utf-8")
            with patch("tools.workspace_io.get_workspace_root", return_value=str(root)):
                from tools.workspace_io import tree_stamp

                first = tree_stamp()["stamp"]
                (root / "b.txt").write_text("2", encoding="utf-8")
                second = tree_stamp()["stamp"]
                self.assertNotEqual(first, second)


class WorkspaceRunTests(unittest.TestCase):
    def test_command_for_python(self):
        from tools.workspace_run import command_for_path

        spec = command_for_path(Path("hello.py"))
        self.assertEqual(spec["runtime"], "Python")
        self.assertEqual(Path(spec["argv"][-1]).name, "hello.py")

    def test_refuses_blocked_exe(self):
        from tools.workspace_run import command_for_path

        with self.assertRaises(ValueError):
            command_for_path(Path("evil.exe"))

    def test_refuses_unsupported_ext(self):
        from tools.workspace_run import command_for_path

        with self.assertRaises(ValueError):
            command_for_path(Path("notes.md"))

    def test_runs_python_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "hi.py").write_text("print('hello-jarvis')\n", encoding="utf-8")
            with patch("tools.workspace_io.get_workspace_root", return_value=str(root)):
                from tools.workspace_run import run_workspace_file

                r = run_workspace_file("hi.py", timeout_sec=15)
                self.assertTrue(r["ok"], r)
                self.assertIn("hello-jarvis", r.get("stdout") or "")
                self.assertEqual(r.get("runtime"), "Python")

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch("tools.workspace_io.get_workspace_root", return_value=str(root)):
                from tools.workspace_run import run_workspace_file

                r = run_workspace_file("../hi.py")
                self.assertFalse(r["ok"])
                self.assertIn("path", (r.get("error") or "").lower())


if __name__ == "__main__":
    unittest.main()
