# -*- coding: utf-8 -*-
"""
Tests that pin the default kill-resilience behavior: resume
from byte N.

User feedback (2026-06-22) when the default was
``restart_incomplete_on_kill=True`` (delete .part on Ctrl-C):

  'should delete the part file of the 9th file and resume from
  downloading the 9th file instead'

The user explicitly wants resume behavior to be the default.
The fix flips the default of ``restart_incomplete_on_kill`` to
False so that Ctrl-C preserves the .part and the next run
resumes from the last downloaded byte.

These tests pin that contract so a future refactor doesn't
flip it back without intent.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDefaultBehaviorIsResume:
    """The default MoodleDlOpts.restart_incomplete_on_kill
    must be False (resume behavior)."""

    def test_default_is_resume_not_restart(self):
        from moodle_dl.types import MoodleDlOpts
        opts = MoodleDlOpts()
        assert opts.restart_incomplete_on_kill is False, (
            'Default restart_incomplete_on_kill must be False '
            '(resume). The user wants Ctrl-C to preserve .part '
            'files for resume, not discard them.'
        )

    def test_post_process_opts_keeps_resume_default(self):
        """With no env var set, post_process_opts must NOT change
        the default (False = resume).
        """
        from moodle_dl.main import post_process_opts
        from moodle_dl.types import MoodleDlOpts
        opts = MoodleDlOpts()
        out = post_process_opts(opts)
        assert out.restart_incomplete_on_kill is False, (
            'Default + no env var must produce resume behavior. '
            'The user should not need to set any flag to get '
            'resume on Ctrl-C.'
        )


class TestEnvVarOptIn:
    """The MOODLE_DL_KEEP_INCOMPLETE_ON_KILL env var now controls
    the OPPOSITE behavior from before:

    * =0: opt into restart-from-scratch (delete .part on Ctrl-C)
    * =1: no-op (matches the new default of resume)
    * unset: use the default (resume)
    """

    def test_env_var_zero_opts_into_restart(self, monkeypatch):
        """MOODLE_DL_KEEP_INCOMPLETE_ON_KILL=0 enables the old
        restart-from-scratch behavior (delete .part on Ctrl-C).
        """
        monkeypatch.setenv('MOODLE_DL_KEEP_INCOMPLETE_ON_KILL', '0')
        from moodle_dl.main import post_process_opts
        from moodle_dl.types import MoodleDlOpts
        opts = MoodleDlOpts()
        out = post_process_opts(opts)
        assert out.restart_incomplete_on_kill is True

    def test_env_var_one_is_no_op_now(self, monkeypatch):
        """With the new default of resume, MOODLE_DL_KEEP_INCOMPLETE_ON_KILL=1
        no longer changes behavior — both env=1 and default = False
        (resume).
        """
        monkeypatch.setenv('MOODLE_DL_KEEP_INCOMPLETE_ON_KILL', '1')
        from moodle_dl.main import post_process_opts
        from moodle_dl.types import MoodleDlOpts
        opts = MoodleDlOpts()
        out = post_process_opts(opts)
        assert out.restart_incomplete_on_kill is False

    def test_env_var_unset_uses_default(self, monkeypatch):
        """Unset env var = use the default (resume)."""
        monkeypatch.delenv('MOODLE_DL_KEEP_INCOMPLETE_ON_KILL', raising=False)
        from moodle_dl.main import post_process_opts
        from moodle_dl.types import MoodleDlOpts
        opts = MoodleDlOpts()
        out = post_process_opts(opts)
        assert out.restart_incomplete_on_kill is False


class TestCLI:
    """CLI flag default behavior matches the type default."""

    def test_cli_default_flag_is_resume(self):
        """Running argparse with no --restart-incomplete-on-kill
        must produce opts.restart_incomplete_on_kill=False."""
        import argparse
        from moodle_dl.main import get_parser
        from moodle_dl.types import MoodleDlOpts

        parser = get_parser()
        # Parse empty args — only sys.argv[0] is set
        ns = parser.parse_args([])
        opts = MoodleDlOpts(**vars(ns))
        assert opts.restart_incomplete_on_kill is False

    def test_cli_keep_incomplete_explicit(self):
        """--keep-incomplete-on-kill explicitly sets to False
        (resume). Same as default but more explicit for scripts."""
        import argparse
        from moodle_dl.main import get_parser
        from moodle_dl.types import MoodleDlOpts

        parser = get_parser()
        ns = parser.parse_args(['--keep-incomplete-on-kill'])
        opts = MoodleDlOpts(**vars(ns))
        assert opts.restart_incomplete_on_kill is False

    def test_cli_restart_incomplete_explicit(self):
        """--restart-incomplete-on-kill explicitly sets to True."""
        import argparse
        from moodle_dl.main import get_parser
        from moodle_dl.types import MoodleDlOpts

        parser = get_parser()
        ns = parser.parse_args(['--restart-incomplete-on-kill'])
        opts = MoodleDlOpts(**vars(ns))
        assert opts.restart_incomplete_on_kill is True


class TestDownloadUrlUsesResumeByDefault:
    """The download_url method's kill handler must default to
    calling _save_incomplete_on_kill (preserve .part for resume),
    not _discard_incomplete_on_kill (delete .part).
    """

    def test_default_uses_save_not_discard(self):
        """When opts.restart_incomplete_on_kill=False (the default),
        download_url's kill handler must call _save_incomplete_on_kill.
        """
        # We can't easily call download_url (it's async + needs
        # network), so we just verify the OPTIONS check by reading
        # the source. The check is:
        #   if getattr(self.opts, 'restart_incomplete_on_kill', False):
        #       await self._discard_incomplete_on_kill(dest_path)
        #   else:
        #       await self._save_incomplete_on_kill(dl_url, dest_path)
        # With the default (False), the else branch runs.
        import inspect
        from moodle_dl.downloader.task import Task
        src = inspect.getsource(Task.download_url)
        # The else branch must reference _save_incomplete_on_kill
        assert '_save_incomplete_on_kill' in src
        assert '_discard_incomplete_on_kill' in src
        # And the check must default to False (resume)
        assert "getattr(self.opts, 'restart_incomplete_on_kill', False)" in src, (
            'download_url must default restart_incomplete_on_kill '
            'to False (resume) so the user-friendly behavior is the '
            'default.'
        )