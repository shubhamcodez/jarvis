"""Unit tests for the SWE harness (no API key)."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from benchmarks.catalog import SWE_FIXTURES, swe_fixture_path
from benchmarks.runner import apply_gold, score_workspace
from tools.overlay_workspace import OverlayWorkspace
from tools.patch_apply import apply_unique_replace


class TestPatchApply(unittest.TestCase):
    def test_unique(self):
        out = apply_unique_replace("a = 1\nb = 2\n", "a = 1", "a = 3")
        self.assertTrue(out["ok"])
        self.assertEqual(out["content"], "a = 3\nb = 2\n")

    def test_missing(self):
        out = apply_unique_replace("a = 1\n", "zzz", "q")
        self.assertFalse(out["ok"])

    def test_ambiguous(self):
        out = apply_unique_replace("x\nx\n", "x", "y")
        self.assertFalse(out["ok"])


class TestOverlay(unittest.TestCase):
    def test_read_write_patch(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-ov-"))
        try:
            (dest / "foo.py").write_text("n = 1\n", encoding="utf-8")
            ws = OverlayWorkspace(dest)
            self.assertIn("foo.py", ws.list_rel_paths())
            ws.apply_patch("foo.py", "n = 1", "n = 2")
            self.assertEqual(ws.raw_text("foo.py"), "n = 2\n")
            self.assertEqual((dest / "foo.py").read_text(encoding="utf-8"), "n = 1\n")
            ws.apply_to_root()
            self.assertEqual((dest / "foo.py").read_text(encoding="utf-8"), "n = 2\n")
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestFixturesGold(unittest.TestCase):
    def test_buggy_fails_gold_passes(self):
        for item in SWE_FIXTURES:
            src = swe_fixture_path(item)
            work = Path(tempfile.mkdtemp(prefix="ada-gold-"))
            try:
                shutil.copytree(
                    src,
                    work,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("gold", "__pycache__"),
                )
                before = score_workspace(work)
                self.assertFalse(before.get("passed"), f"{item['id']} should fail before gold")
                apply_gold(src, work)
                after = score_workspace(work)
                self.assertTrue(after.get("passed"), f"{item['id']} gold should pass: {after.get('summary')}")
            finally:
                shutil.rmtree(work, ignore_errors=True)


class TestSkills(unittest.TestCase):
    def test_discover_repo_skills(self):
        from tools.skills import discover_skills, select_skills, skills_for_prompt

        root = str(BACKEND.parent)
        skills = discover_skills(root)
        names = {s["name"] for s in skills}
        self.assertIn("swe-fix", names)
        matched = select_skills(skills, "use swe-fix on this failing test")
        self.assertTrue(matched)
        text = skills_for_prompt("fix the failing tests in this repo", root)
        self.assertIn("swe-fix", text)


class TestRecap(unittest.TestCase):
    def test_empty_recap(self):
        from memory.chat_search import recap_markdown

        md = recap_markdown("does-not-exist")
        self.assertIn("# Recap", md)


class TestLsp(unittest.TestCase):
    def test_undefined_name(self):
        from tools.ada_pylsp import analyze_python

        diags = analyze_python("print(not_defined_xyz)\n")
        self.assertTrue(any("not_defined_xyz" in d["message"] for d in diags))

    def test_clean_python(self):
        from tools.ada_pylsp import analyze_python

        self.assertEqual(analyze_python("x = 1\nprint(x)\n"), [])

    def test_lsp_client_on_overlay(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-lsp-"))
        try:
            (dest / "bad.py").write_text("print(missing_symbol_qqq)\n", encoding="utf-8")
            ws = OverlayWorkspace(dest)
            from tools.lsp_client import run_lsp_diagnostics

            out = run_lsp_diagnostics(ws, ["bad.py"], timeout_sec=6.0)
            msgs = " ".join(d.get("message") or "" for d in out.get("diagnostics") or [])
            self.assertIn("missing_symbol_qqq", msgs)
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestPreviewConvention(unittest.TestCase):
    def test_html_preview_fence(self):
        from tools.sandbox_markdown import stdout_to_markdown_body

        md = stdout_to_markdown_body("ADA_PREVIEW_HTML:<h1>Hi</h1>\n")
        self.assertIn("```html", md)
        self.assertIn("<h1>Hi</h1>", md)


class TestChatFork(unittest.TestCase):
    def test_fork_and_merge(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-chats-"))
        try:
            with patch("memory.chat_log.chats_dir", return_value=dest):
                from memory.chat_log import append_chat_log, create_new_chat, fork_chat, merge_branch, read_chat_log

                cid = create_new_chat()
                append_chat_log("user", "hello", cid)
                append_chat_log("assistant", "hi there", cid)
                append_chat_log("user", "try another way", cid)
                child = fork_chat(cid, 1, "alt")
                self.assertTrue(child.get("ok"))
                append_chat_log("user", "branch work", child["id"])
                append_chat_log("assistant", "branch answer", child["id"])
                merged = merge_branch(child["id"], cid)
                self.assertTrue(merged.get("ok"))
                parent = read_chat_log(cid)
                self.assertTrue(any("Merged branch" in (m.get("content") or "") for m in parent))
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestSlashCommands(unittest.TestCase):
    def test_expand_arguments(self):
        from tools.slash_commands import expand_command

        self.assertIn("64", expand_command("Fix issue $ARGUMENTS", "64"))
        self.assertIn("hello", expand_command("No placeholder", "hello"))

    def test_discover_command_and_skill_resolve(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-cmd-"))
        try:
            (dest / ".agents" / "commands").mkdir(parents=True)
            (dest / ".agents" / "commands" / "fix-issue.md").write_text(
                "---\nname: fix-issue\ndescription: Fix a ticket\n---\nWork on $ARGUMENTS\n",
                encoding="utf-8",
            )
            from tools.slash_commands import discover_commands, resolve_named

            names = {c["name"] for c in discover_commands(str(dest))}
            self.assertIn("fix-issue", names)
            hit = resolve_named("fix-issue", "42", str(dest))
            self.assertTrue(hit.get("ok"))
            self.assertEqual(hit.get("kind"), "command")
            self.assertIn("42", hit.get("prompt") or "")
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestAgentsInit(unittest.TestCase):
    def test_write_once(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-init-"))
        try:
            (dest / "app.py").write_text("print(1)\n", encoding="utf-8")
            from tools.agents_init import draft_agents_md, write_agents_md

            draft = draft_agents_md(str(dest))
            self.assertTrue(draft.get("ok"))
            self.assertIn("AGENTS.md", draft.get("markdown") or "")
            first = write_agents_md(workspace_root=str(dest))
            self.assertTrue(first.get("written"))
            second = write_agents_md(workspace_root=str(dest))
            self.assertFalse(second.get("written"))
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestRewind(unittest.TestCase):
    def test_unanswered_user_does_not_drop_prior_assistant(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-rew-"))
        try:
            with patch("memory.chat_log.chats_dir", return_value=dest):
                from memory.chat_log import append_chat_log, create_new_chat, read_chat_log, rewind_chat

                cid = create_new_chat()
                append_chat_log("user", "hello", cid)
                append_chat_log("assistant", "hi", cid)
                append_chat_log("user", "again", cid)
                out = rewind_chat(cid)
                self.assertTrue(out.get("ok"), out)
                msgs = read_chat_log(cid)
                roles = [(m.get("role"), m.get("content")) for m in msgs]
                self.assertEqual(roles, [("user", "hello"), ("assistant", "hi")])
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    def test_drop_last_assistant(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-rew-"))
        try:
            with patch("memory.chat_log.chats_dir", return_value=dest):
                from memory.chat_log import append_chat_log, create_new_chat, read_chat_log, rewind_chat

                cid = create_new_chat()
                append_chat_log("user", "hello", cid)
                append_chat_log("assistant", "hi", cid)
                append_chat_log("user", "again", cid)
                append_chat_log("assistant", "ok", cid)
                out = rewind_chat(cid)
                self.assertTrue(out.get("ok"))
                msgs = read_chat_log(cid)
                self.assertEqual(msgs[-1]["role"], "user")
                self.assertEqual(msgs[-1]["content"], "again")
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestGitHistory(unittest.TestCase):
    def test_log_and_recent(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-git-"))
        try:
            import subprocess

            if not shutil.which("git"):
                self.skipTest("git not installed")
            subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "ada@test"], cwd=dest, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Jarvis"], cwd=dest, check=True, capture_output=True)
            (dest / "mod.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "mod.py"], cwd=dest, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "commit", "-m", "init"],
                cwd=dest,
                check=True,
                capture_output=True,
            )
            from tools.git_history import git_history, recent_touched_files

            log = git_history(dest, "log", limit=3)
            self.assertTrue(log.get("ok"), log)
            self.assertIn("init", log.get("output") or "")
            self.assertIn("mod.py", recent_touched_files(dest, limit=4))
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestSlashRuntime(unittest.TestCase):
    def test_doctor_markdown(self):
        from tools.doctor import doctor_markdown

        md = doctor_markdown()
        self.assertIn("# Doctor", md)
        self.assertIn("python", md)

    def test_rename_export_copy_context(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-exp-"))
        try:
            with patch("memory.chat_log.chats_dir", return_value=dest):
                from memory.chat_log import (
                    append_chat_log,
                    chat_context_stats,
                    create_new_chat,
                    export_chat_markdown,
                    last_assistant_text,
                    list_chats,
                    rename_chat,
                )

                cid = create_new_chat()
                append_chat_log("user", "hello there", cid)
                append_chat_log("assistant", "hi friend", cid)
                renamed = rename_chat(cid, "Sprint notes")
                self.assertEqual(renamed.get("title"), "Sprint notes")
                titles = {c["title"] for c in list_chats()}
                self.assertIn("Sprint notes", titles)
                exp = export_chat_markdown(cid)
                self.assertTrue(exp.get("ok"))
                self.assertIn("hi friend", exp.get("markdown") or "")
                copied = last_assistant_text(cid, 1)
                self.assertEqual(copied.get("text"), "hi friend")
                stats = chat_context_stats(cid)
                self.assertGreater(stats.get("tokens") or 0, 0)
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestSessionLoops(unittest.TestCase):
    def test_parse_and_start_stop(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-loop-"))
        try:
            with patch("tools.session_loops.data_root", return_value=dest), patch(
                "tools.session_loops.chats_dir", return_value=dest
            ):
                from tools.session_loops import list_loops, parse_interval, start_loop, stop_loops

                self.assertEqual(parse_interval("30s"), 30)
                self.assertEqual(parse_interval("5m"), 300)
                self.assertEqual(parse_interval("1h"), 3600)
                self.assertIsNone(parse_interval("check deploy"))
                out = start_loop("chat1", 60, "check deploy")
                self.assertTrue(out.get("ok"))
                self.assertEqual(len(list_loops("chat1")), 1)
                self.assertEqual(stop_loops("chat1"), 1)
                self.assertEqual(list_loops("chat1"), [])
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestChatGoal(unittest.TestCase):
    def test_set_and_clear(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-goal-"))
        try:
            with patch("memory.chat_log.chats_dir", return_value=dest):
                from memory.chat_log import create_new_chat, get_chat_meta, set_chat_goal

                cid = create_new_chat()
                set_chat_goal(cid, "tests pass")
                self.assertEqual(get_chat_meta(cid).get("goal_condition"), "tests pass")
                set_chat_goal(cid, "")
                self.assertEqual(get_chat_meta(cid).get("goal_condition"), "")
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestMermaidPreview(unittest.TestCase):
    def test_mermaid_fence(self):
        from tools.sandbox_markdown import stdout_to_markdown_body

        md = stdout_to_markdown_body("ADA_PREVIEW_MERMAID:graph TD; A --> B\n")
        self.assertIn("```mermaid", md)
        self.assertIn("A --> B", md)


class TestSweRouting(unittest.TestCase):
    def test_linked_folder_prefers_swe(self):
        from agents.coding_agent import _use_swe_loop

        self.assertTrue(_use_swe_loop("implement login", r"D:\proj", ""))
        self.assertTrue(_use_swe_loop("fix the failing tests", r"D:\proj", ""))
        self.assertFalse(_use_swe_loop("what is this module", r"D:\proj", ""))
        self.assertFalse(_use_swe_loop("plot a histogram of returns", r"D:\proj", ""))
        self.assertFalse(_use_swe_loop("implement login", "", ""))


class TestWriteFileReason(unittest.TestCase):
    def test_existing_file_needs_reason(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-wf-"))
        try:
            (dest / "foo.py").write_text("n = 1\n", encoding="utf-8")
            ws = OverlayWorkspace(dest)
            from agents.swe_tools import dispatch

            denied = dispatch(ws, "write_file", {"path": "foo.py", "content": "n = 2\n"}, run_id="t", plan=[])
            self.assertFalse(denied.get("ok"))
            self.assertIn("reason", (denied.get("error") or "").lower())
            ok = dispatch(
                ws,
                "write_file",
                {"path": "foo.py", "content": "n = 2\n", "reason": "replace stub"},
                run_id="t",
                plan=[],
            )
            self.assertTrue(ok.get("ok"), ok)
            self.assertEqual(ws.raw_text("foo.py"), "n = 2\n")
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestOverlayCheckpoint(unittest.TestCase):
    def test_originals_and_restore(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-cp-"))
        chats = Path(tempfile.mkdtemp(prefix="ada-chats-"))
        try:
            (dest / "foo.py").write_text("n = 1\n", encoding="utf-8")
            ws = OverlayWorkspace(dest)
            ws.apply_patch("foo.py", "n = 1", "n = 2")
            snap = ws.originals_for_changed()
            self.assertEqual(snap["foo.py"]["before"], "n = 1\n")
            self.assertEqual(snap["foo.py"]["after"], "n = 2\n")
            with patch("memory.chat_log.chats_dir", return_value=chats), patch(
                "agents.run_control.chats_dir", return_value=chats
            ), patch("tools.workspace_io.get_workspace_root", return_value=str(dest)), patch(
                "config.get_workspace_root", return_value=str(dest)
            ), patch(
                "agents.execution_policy.deny_if_blocked", return_value=None
            ):
                from agents.run_control import record_checkpoint, restore_checkpoint

                rec = record_checkpoint("run1", kind="overlay_turn", summary="test", payload=snap)
                self.assertTrue(rec and rec.get("id"))
                ws.apply_to_root()
                self.assertEqual((dest / "foo.py").read_text(encoding="utf-8"), "n = 2\n")
                out = restore_checkpoint(rec["id"])
                self.assertTrue(out.get("ok"), out)
                self.assertEqual((dest / "foo.py").read_text(encoding="utf-8"), "n = 1\n")
        finally:
            shutil.rmtree(dest, ignore_errors=True)
            shutil.rmtree(chats, ignore_errors=True)


class TestDiscoverTestCommand(unittest.TestCase):
    def test_npm_when_only_package_json(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-npm-"))
        try:
            (dest / "package.json").write_text(
                '{"scripts":{"test":"node -e \\"process.exit(0)\\""}}',
                encoding="utf-8",
            )
            from tools.diagnostics import discover_test_command

            with patch("tools.diagnostics.shutil.which", return_value="npm"):
                cmd = discover_test_command(dest)
            self.assertEqual(cmd[:2], ["npm", "test"])
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    def test_pytest_when_tests_dir(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-pyt-"))
        try:
            (dest / "tests").mkdir()
            (dest / "tests" / "test_a.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            from tools.diagnostics import discover_test_command

            cmd = discover_test_command(dest)
            self.assertIn("pytest", " ".join(cmd))
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    def test_unittest_suite_skips_pytest(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-ut-"))
        try:
            (dest / "tests").mkdir()
            (dest / "tests" / "test_a.py").write_text(
                "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            from tools.diagnostics import discover_test_command

            cmd = discover_test_command(dest)
            joined = " ".join(cmd)
            self.assertIn("unittest", joined)
            self.assertNotIn("pytest", joined)
            self.assertIn("-t", cmd)
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    def test_pytest_style_still_runs(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-pyt-run-"))
        try:
            (dest / "tests").mkdir()
            (dest / "tests" / "test_a.py").write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
            out = score_workspace(dest)
            self.assertIn("pytest", out.get("command") or "")
            self.assertTrue(out.get("passed"), out.get("summary"))
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestWarmUnittestRunner(unittest.TestCase):
    def _copy(self, src: Path, *, gold: bool) -> Path:
        work = Path(tempfile.mkdtemp(prefix="ada-warm-"))
        shutil.copytree(src, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("gold", "__pycache__"))
        if gold:
            apply_gold(src, work)
        return work

    def test_reuses_worker_without_stale_imports(self):
        item = next(entry for entry in SWE_FIXTURES if entry["id"] == "mean_bias")
        src = swe_fixture_path(item)
        trees: list[Path] = []
        try:
            buggy = self._copy(src, gold=False)
            fixed = self._copy(src, gold=True)
            buggy_again = self._copy(src, gold=False)
            trees.extend([buggy, fixed, buggy_again])
            first = score_workspace(buggy)
            second = score_workspace(fixed)
            third = score_workspace(buggy_again)
            self.assertFalse(first.get("passed"), first.get("summary"))
            self.assertTrue(second.get("passed"), second.get("summary"))
            self.assertFalse(third.get("passed"), third.get("summary"))
            self.assertIn("unittest", first.get("command") or "")
            self.assertIn("ZeroDivisionError", first.get("summary") or "")
        finally:
            for tree in trees:
                shutil.rmtree(tree, ignore_errors=True)

    def test_zero_collected_tests_do_not_pass(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-empty-"))
        try:
            (dest / "tests").mkdir()
            (dest / "tests" / "test_empty.py").write_text(
                "import unittest\n\nclass T(unittest.TestCase):\n    pass\n",
                encoding="utf-8",
            )
            out = score_workspace(dest)
            self.assertFalse(out.get("passed"), out)
            self.assertIn("No tests ran", out.get("summary") or "")
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestChatPlan(unittest.TestCase):
    def test_set_and_read(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-plan-"))
        try:
            with patch("memory.chat_log.chats_dir", return_value=dest):
                from memory.chat_log import create_new_chat, get_chat_meta, set_chat_plan

                cid = create_new_chat()
                set_chat_plan(cid, [{"id": "1", "status": "active", "text": "read tests"}])
                meta = get_chat_meta(cid)
                self.assertEqual(meta.get("plan")[0]["text"], "read tests")
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestWorkspaceFileEdits(unittest.TestCase):
    def test_same_line_closer_is_extracted(self):
        from tools.workspace_file_edits import extract_workspace_file_edits

        text = "```jarvis-file:pkg/hello.py\nprint('hi')```\n"
        clean, edits = extract_workspace_file_edits(text)
        self.assertEqual(len(edits), 1, f"expected one edit, got {edits!r} clean={clean!r}")
        self.assertEqual(edits[0]["path"], "pkg/hello.py")
        self.assertIn("print('hi')", edits[0]["content"])

    def test_empty_file_fence_is_extracted(self):
        from tools.workspace_file_edits import extract_workspace_file_edits

        text = "```jarvis-file:empty.txt\n```\n"
        clean, edits = extract_workspace_file_edits(text)
        self.assertEqual(len(edits), 1, f"expected empty-file edit, got {edits!r} clean={clean!r}")
        self.assertEqual(edits[0]["path"], "empty.txt")

    def test_standard_fence_still_works(self):
        from tools.workspace_file_edits import extract_workspace_file_edits

        text = "```jarvis-file:a.py\nx = 1\n```\n"
        clean, edits = extract_workspace_file_edits(text)
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["path"], "a.py")
        self.assertIn("x = 1", edits[0]["content"])


class TestSlashCatalog(unittest.TestCase):
    def test_builtins_include_goal(self):
        from tools.slash_commands import catalog

        names = {c["name"] for c in catalog().get("builtins") or []}
        self.assertIn("goal", names)
        self.assertIn("undo", names)


if __name__ == "__main__":
    unittest.main()


