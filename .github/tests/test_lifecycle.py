import contextlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/scripts"))
import release
import source_version as source


@contextlib.contextmanager
def cwd(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def git(path, *args):
    location = ["--git-dir", str(path)] if path.name.endswith(".git") else ["-C", str(path)]
    return subprocess.run(
        ["git", *location, *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def copy_source(destination):
    for path in source.SOURCE_PATHS:
        (destination / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination / path)


def entry(tag="v9.8.7", draft=False):
    return {"id": 42, "tag_name": tag, "draft": draft, "prerelease": False}


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        copy_source(self.root)
        self.current = source.read(self.root)

    def plist(self, **values):
        path = self.root / source.PLIST
        data = plistlib.loads(path.read_bytes())
        data.update(values)
        path.write_bytes(plistlib.dumps(data))

    def test_live_metadata_and_all_bump_modes(self):
        parts = list(map(int, self.current.split(".")))
        for mode, expected in (
            ("patch", [parts[0], parts[1], parts[2] + 1]),
            ("minor", [parts[0], parts[1] + 1, 0]),
            ("major", [parts[0] + 1, 0, 0]),
        ):
            with self.subTest(mode=mode):
                copy_source(self.root)
                expected = ".".join(map(str, expected))
                self.assertEqual(source.bump(mode, self.root), expected)
                self.assertEqual(source.read(self.root), expected)
                self.assertNotIn("CFBundleVersion",
                                 plistlib.loads((self.root / source.PLIST).read_bytes()))

    def test_invalid_missing_and_drifted_project_settings(self):
        path = self.root / source.PROJECT
        original = path.read_text()
        for key in source.KEYS:
            for bad in ("1.2", "v1.2.3", "01.2.3", "1.2.3-beta", "$(UNKNOWN)", "", "0.0.0"):
                with self.subTest(key=key, bad=bad):
                    path.write_text(original.replace(f"{key} = {self.current};",
                                                     f"{key} = {bad};", 1))
                    with self.assertRaises(ValueError):
                        source.bump("patch", self.root)
            path.write_text(original.replace(f"{key} = {self.current};", "", 1))
            with self.assertRaises(ValueError):
                source.read(self.root)

    def test_plist_invalid_drift_and_missing(self):
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            for value in ("0.0.0", "$(UNKNOWN)", "$(PRODUCT_NAME)", "", "1.2", 123):
                with self.subTest(key=key, value=value):
                    copy_source(self.root)
                    self.plist(**{key: value})
                    with self.assertRaises(ValueError):
                        source.read(self.root)
        copy_source(self.root)
        path = self.root / source.PLIST
        data = plistlib.loads(path.read_bytes())
        del data["CFBundleShortVersionString"]
        path.write_bytes(plistlib.dumps(data))
        with self.assertRaises(ValueError):
            source.read(self.root)

    def test_valid_refs_preserved_and_optional_bundle_version_coordinated(self):
        for reference in source.REFERENCES:
            with self.subTest(reference=reference):
                copy_source(self.root)
                self.plist(CFBundleShortVersionString=reference, CFBundleVersion=reference)
                path = self.root / source.PROJECT
                path.write_text(path.read_text().replace(
                    f"CURRENT_PROJECT_VERSION = {self.current};",
                    'CURRENT_PROJECT_VERSION = "$(MARKETING_VERSION)";'
                ))
                new = source.bump("patch", self.root)
                data = plistlib.loads((self.root / source.PLIST).read_bytes())
                self.assertEqual(data["CFBundleShortVersionString"], reference)
                self.assertEqual(data["CFBundleVersion"], reference)
                self.assertEqual(path.read_text().count('"$(MARKETING_VERSION)"'), 6)
                self.assertEqual(source.read(self.root), new)
        copy_source(self.root)
        self.plist(CFBundleVersion=self.current)
        new = source.bump("minor", self.root)
        self.assertEqual(plistlib.loads((self.root / source.PLIST).read_bytes())["CFBundleVersion"], new)

    def test_cyclic_current_project_ref_rejected(self):
        path = self.root / source.PROJECT
        path.write_text(path.read_text().replace(
            f"CURRENT_PROJECT_VERSION = {self.current};",
            'CURRENT_PROJECT_VERSION = "$(CURRENT_PROJECT_VERSION)";'
        ))
        with self.assertRaises(ValueError):
            source.read(self.root)

    def test_prebuilt_schema_and_localization_counters_ignored(self):
        excluded = self.root / "Frontmost.4dbase/Plugins/Frontmost.bundle/Contents/Info.plist"
        excluded.parent.mkdir(parents=True)
        excluded.write_text("prebuilt 0.0.0")
        for name in ("manifest.json", "English.lproj/locversion.plist"):
            (self.root / name).write_text("independent revision 14")
        source.bump("major", self.root)
        self.assertEqual(excluded.read_text(), "prebuilt 0.0.0")
        for name in ("manifest.json", "English.lproj/locversion.plist"):
            self.assertEqual((self.root / name).read_text(), "independent revision 14")

    def test_localized_version_validation_and_bumping(self):
        path = self.root / source.LOCALIZED
        original = path.read_text(encoding="utf-8-sig")
        for malformed in (
            original.replace(f'CFBundleShortVersionString = "{self.current}"',
                             'CFBundleShortVersionString = "1.0"'),
            original.replace(f"Frontmost version {self.current},", "Frontmost version invalid,"),
            original + f'\nCFBundleShortVersionString = "{self.current}";\n',
            original.replace("CFBundleShortVersionString", "UnknownKey"),
        ):
            with self.subTest(malformed=malformed):
                path.write_text(malformed, encoding="utf-8-sig")
                with self.assertRaises(ValueError):
                    source.read(self.root)
        path.write_text(original, encoding="utf-8-sig")
        new = source.bump("minor", self.root)
        self.assertIn(f'CFBundleShortVersionString = "{new}";', path.read_text(encoding="utf-8-sig"))
        self.assertIn(f"Frontmost version {new}, Copyright 2017 miyako.",
                      path.read_text(encoding="utf-8-sig"))
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))


class AvailabilityTests(unittest.TestCase):
    def test_paginated_draft_and_published_conflicts(self):
        for draft in (True, False):
            with self.subTest(draft=draft), patch.object(release, "remote_ref", return_value=None), \
                    patch.object(release, "api", side_effect=[
                        [entry()] * 100, [entry("v1.2.3", draft)]
                    ]) as api:
                with self.assertRaisesRegex(ValueError, "draft or published"):
                    release.available("owner/repo", "v1.2.3")
                self.assertEqual(api.call_args_list[1].args[0],
                                 "repos/owner/repo/releases?per_page=100&page=2")

    def test_complete_successful_no_match(self):
        with patch.object(release, "remote_ref", return_value=None), \
                patch.object(release, "api", side_effect=[[entry()] * 100, []]) as api:
            release.available("owner/repo", "v1.2.3")
            self.assertEqual(api.call_count, 2)

    def test_errors_on_every_page_fail_closed(self):
        for page in (1, 2):
            for error in (*(subprocess.CalledProcessError(1, ["gh", "api", str(status)])
                            for status in (401, 403, 404, 500)),
                          OSError("network"), json.JSONDecodeError("bad", "?", 0)):
                with self.subTest(page=page, error=error), \
                        patch.object(release, "remote_ref", return_value=None), \
                        patch.object(release, "api", side_effect=(
                            [[entry()] * 100] * (page - 1) + [error])):
                    with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)):
                        release.available("owner/repo", "v1.2.3")

    def test_malformed_page_and_entry_schema(self):
        bad_pages = [{}, None, "[]", [None], [{}], [dict(entry(), id=True)],
                     [dict(entry(), id=0)], [dict(entry(), tag_name=None)],
                     [dict(entry(), draft="false")], [dict(entry(), prerelease=None)],
                     [entry()] * 101]
        for page in (1, 2):
            for bad in bad_pages:
                with self.subTest(page=page, bad=bad), \
                        patch.object(release, "remote_ref", return_value=None), \
                        patch.object(release, "api", side_effect=(
                            [[entry()] * 100] * (page - 1) + [bad])):
                    with self.assertRaises(ValueError):
                        release.available("owner/repo", "v1.2.3")

    def test_existing_remote_tag_blocks_without_api(self):
        with patch.object(release, "remote_ref", return_value="a" * 40), \
                patch.object(release, "api") as api:
            with self.assertRaises(ValueError):
                release.available("owner/repo", "v1.2.3")
            api.assert_not_called()

    def test_api_transport_explicit_get_post_no_retry(self):
        with patch.object(release, "run", return_value="[]") as run:
            release.api("repos/o/r/releases?per_page=100&page=2")
            self.assertEqual(run.call_args.args,
                             ("gh", "api", "--method", "GET",
                              "repos/o/r/releases?per_page=100&page=2"))
        with patch.object(release, "run", return_value='{"id": 7}') as run:
            payload = {"tag_name": "v1.2.3", "draft": False}
            release.api("repos/o/r/releases", payload)
            self.assertIn("POST", run.call_args.args)
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), payload)
        with patch.object(release, "run", side_effect=subprocess.CalledProcessError(1, "gh")) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                release.api("repos/o/r/releases")
            self.assertEqual(run.call_count, 1)
        with patch.object(release, "run", return_value='{"id": 8}') as run:
            release.api("https://uploads.github.com/repos/o/r/releases/123/assets?name=asset.zip",
                        asset=Path("asset.zip"))
            self.assertEqual(run.call_args.args, (
                "gh", "api", "--method", "POST",
                "https://uploads.github.com/repos/o/r/releases/123/assets?name=asset.zip",
                "--header", "Content-Type: application/octet-stream", "--input", "asset.zip",
            ))

    def test_remote_ref_errors_and_malformed_output(self):
        for output in ("not-a-sha\trefs/tags/v1.2.3", "a" * 40,
                       "a" * 40 + "\trefs/tags/wrong",
                       ("a" * 40 + "\trefs/tags/v1.2.3\n") * 2):
            with self.subTest(output=output), patch.object(release, "run", return_value=output):
                with self.assertRaises(ValueError):
                    release.remote_ref("refs/tags/v1.2.3")
        with patch.object(release, "run", side_effect=OSError("network")), \
                patch.object(release, "api") as api:
            with self.assertRaises(OSError):
                release.available("o/r", "v1.2.3")
        api.assert_not_called()


class GitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.local = self.root / "local"
        self.peer = self.root / "peer"
        git(self.root, "init", "--bare", str(self.remote))
        git(self.root, "init", "-b", "selected", str(self.local))
        for key, value in (("user.name", "Lifecycle Test"), ("user.email", "test@example.invalid"),
                           ("push.followTags", "true")):
            git(self.local, "config", key, value)
        git(self.local, "commit", "--allow-empty", "-m", "ancestor")
        self.ancestor = git(self.local, "rev-parse", "HEAD")
        copy_source(self.local)
        shutil.copytree(ROOT / ".github/scripts", self.local / ".github/scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / ".gitignore", self.local / ".gitignore")
        git(self.local, "add", ".")
        git(self.local, "commit", "-m", "fixture")
        git(self.local, "remote", "add", "origin", str(self.remote))
        git(self.local, "push", "origin", "HEAD:refs/heads/selected")
        self.sha = git(self.local, "rev-parse", "HEAD")
        self.version = source.read(self.local)
        self.tag = f"v{self.version}"
        self.published = None
        self.uploaded = []
        git(self.root, "clone", "--branch", "selected", str(self.remote), str(self.peer))
        git(self.peer, "config", "user.name", "Peer")
        git(self.peer, "config", "user.email", "peer@example.invalid")
        self.enter = cwd(self.local)
        self.enter.__enter__()
        self.addCleanup(self.enter.__exit__, None, None, None)
        # Exercise the guard from this fixture, not the parent working tree.
        self.module_path = patch.object(release, "__file__",
                                        str(self.local / ".github/scripts/release.py"))
        self.module_path.start()
        self.addCleanup(self.module_path.stop)

    def advance_peer(self):
        (self.peer / "peer.txt").write_text("concurrent branch update")
        git(self.peer, "add", ".")
        git(self.peer, "commit", "-m", "peer")
        git(self.peer, "push", "origin", "HEAD:refs/heads/selected")

    def test_bump_selected_branch_disables_follow_tags(self):
        git(self.local, "tag", "-a", "unrelated-annotated", "-m", "must not follow")
        git(self.remote, "update-ref", "refs/heads/other", self.sha)
        release.bump_branch(self.sha, "selected", "patch")
        self.assertNotEqual(git(self.remote, "rev-parse", "refs/heads/selected"), self.sha)
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/other"), self.sha)
        self.assertEqual(git(self.remote, "tag", "--list"), "")
        self.assertEqual(set(git(self.local, "diff-tree", "--no-commit-id", "--name-only",
                                 "-r", "HEAD").splitlines()), set(map(str, source.SOURCE_PATHS)))
        self.assertEqual(git(self.local, "status", "--porcelain"), "")

    def test_bump_cli_clean_with_bytecode_enabled(self):
        env = os.environ.copy()
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        subprocess.run(
            [sys.executable, ".github/scripts/release.py", "bump", "--sha", self.sha,
             "--branch", "selected", "--mode", "patch"],
            env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(git(self.local, "status", "--porcelain"), "")
        self.assertNotEqual(git(self.remote, "rev-parse", "refs/heads/selected"), self.sha)
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_branch_moved_before_preparation(self):
        self.advance_peer()
        with self.assertRaisesRegex(ValueError, "moved"):
            release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)

    def test_dirty_source_rejected(self):
        (self.local / source.PLIST).write_text("dirty")
        with self.assertRaisesRegex(ValueError, "clean"):
            release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(release.remote_ref("refs/heads/selected"), self.sha)

    def test_build_rejects_dirty_source_before_remote_calls(self):
        path = self.local / source.PLIST
        path.write_text(path.read_text().replace(self.version, "0.0.0"))
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api") as api:
            with self.assertRaises(subprocess.CalledProcessError):
                release.preflight(self.sha)
        api.assert_not_called()
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_branch_moved_during_preparation(self):
        real_bump = release.bump
        def concurrent_bump(mode):
            result = real_bump(mode)
            self.advance_peer()
            return result
        with patch.object(release, "bump", side_effect=concurrent_bump):
            with self.assertRaisesRegex(ValueError, "moved during"):
                release.bump_branch(self.sha, "selected", "minor")
        self.assertEqual(release.remote_ref("refs/heads/selected"), git(self.peer, "rev-parse", "HEAD"))

    def advertised_race(self, kind):
        real_run = release.run
        expected = None
        pushes = 0
        checks = 0
        real_remote_ref = release.remote_ref
        def checked_remote(ref):
            nonlocal checks
            checks += 1
            return real_remote_ref(ref)
        def concurrent_push(*args, **kwargs):
            nonlocal expected, pushes
            if "push" in args:
                pushes += 1
                # This mutation is AFTER both ls-remote checks, BEFORE receive-pack.
                self.assertEqual(checks, 2)
                if kind == "rewind":
                    expected = self.ancestor
                    git(self.remote, "update-ref", "refs/heads/selected", expected)
                elif kind == "delete":
                    git(self.remote, "update-ref", "-d", "refs/heads/selected")
                elif kind == "advance":
                    self.advance_peer()
                    expected = git(self.peer, "rev-parse", "HEAD")
                elif kind == "same-result":
                    expected = git(self.local, "rev-parse", "HEAD")
                    git(self.remote, "fetch", "--no-tags", str(self.local), "HEAD")
                    git(self.remote, "update-ref", "refs/heads/selected", expected)
                else:
                    self.fail(f"Unknown race: {kind}")
            return real_run(*args, **kwargs)
        with patch.object(release, "run", side_effect=concurrent_push), \
                patch.object(release, "remote_ref", side_effect=checked_remote):
            with self.assertRaises(subprocess.CalledProcessError):
                release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(pushes, 1)
        self.assertEqual(release.remote_ref("refs/heads/selected"), expected)
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_advertised_branch_rewind_rejected(self):
        self.advertised_race("rewind")

    def test_advertised_branch_deletion_not_recreated(self):
        self.advertised_race("delete")

    def test_advertised_branch_advance_rejected(self):
        self.advertised_race("advance")

    def test_advertised_same_result_rejected(self):
        self.advertised_race("same-result")

    def test_missing_or_nonexecutable_guard_fails_closed(self):
        guard = self.local / ".github/scripts/bump-hooks/pre-push"
        guard.chmod(0o644)
        for absent in (False, True):
            if absent:
                guard.unlink()
            with self.subTest(absent=absent), self.assertRaisesRegex(
                ValueError, "guard is missing or not executable"
            ):
                release.bump_branch(self.sha, "selected", "patch")
            self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)
            self.assertEqual(release.remote_ref("refs/heads/selected"), self.sha)

    def test_immutable_sha_and_invalid_branch(self):
        for sha in ("selected", "a" * 40, "a" * 39, "a" * 41):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                release.selected_sha(sha)
        release.selected_sha(self.sha)
        for branch in ("selected:other", "bad..branch", "missing"):
            with self.subTest(branch=branch), self.assertRaises(
                (ValueError, subprocess.CalledProcessError)
            ):
                release.bump_branch(self.sha, branch, "patch")
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)

    def assets(self):
        paths = [Path(f"Frontmost-{self.version}.{ext}") for ext in ("zip", "dmg")]
        for asset in paths:
            asset.write_bytes(b"fixture asset")
        return paths

    def publication_api(self, calls, fail=None, race=None, malformed=None):
        def api(endpoint, payload=None, asset=None):
            calls.append((endpoint, payload, asset))
            if "/releases?" in endpoint:
                return []
            if endpoint.endswith("/git/refs"):
                if race:
                    git(self.remote, "update-ref", f"refs/tags/{self.tag}", race)
                # Model REST create-only with a real atomic bare-Git operation.
                git(self.remote, "update-ref", payload["ref"], payload["sha"], "0" * 40)
                return {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
            if endpoint.endswith("/releases"):
                if fail == "release":
                    raise subprocess.CalledProcessError(1, ["gh", "api"])
                self.published = entry(self.tag)
                return {} if malformed == "release" else self.published
            if asset is not None:
                if fail == asset.suffix:
                    raise subprocess.CalledProcessError(1, ["gh", "api"])
                self.uploaded.append(asset.name)
                return {} if malformed == "asset" else {
                    "id": 456, "name": asset.name, "size": asset.stat().st_size, "state": "uploaded"
                }
            raise AssertionError(endpoint)
        return api

    def test_publication_create_only_exact_sha_and_new_release_id(self):
        calls = []
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", side_effect=self.publication_api(calls)):
            release.publish(self.sha, self.assets())
        self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), self.sha)
        self.assertIn("/releases?", calls[0][0])
        self.assertTrue(calls[1][0].endswith("/git/refs"))
        self.assertEqual(calls[2][1], {
            "tag_name": self.tag, "target_commitish": self.sha, "name": self.tag,
            "draft": False, "prerelease": False, "body": "",
        })
        self.assertEqual(len(calls), 5)
        for endpoint, payload, asset in calls[3:]:
            self.assertIn("/releases/42/assets?name=", endpoint)
            self.assertIsNone(payload)
            self.assertIsNotNone(asset)

    def tag_race(self, sha):
        calls = []
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", side_effect=self.publication_api(calls, race=sha)):
            with self.assertRaises(subprocess.CalledProcessError):
                release.publish(self.sha, self.assets())
        self.assertEqual(len(calls), 2)
        self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), sha)
        self.assertIsNone(self.published)

    def test_same_sha_tag_creation_race_rejected(self):
        self.tag_race(self.sha)

    def test_different_sha_tag_creation_race_rejected(self):
        self.advance_peer()
        self.tag_race(git(self.peer, "rev-parse", "HEAD"))

    def failure_retention(self, fail=None, malformed=None):
        calls = []
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", side_effect=self.publication_api(
                    calls, fail=fail, malformed=malformed)):
            with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                release.publish(self.sha, self.assets())
        self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), self.sha)
        return calls

    def test_release_post_failure_retains_tag_without_upload(self):
        self.assertEqual(len(self.failure_retention(fail="release")), 3)
        self.assertIsNone(self.published)
        self.assertEqual(self.uploaded, [])

    def test_first_upload_failure_retains_public_release(self):
        self.assertEqual(len(self.failure_retention(fail=".zip")), 4)
        self.assertEqual(self.published["id"], 42)
        self.assertEqual(self.uploaded, [])

    def test_second_upload_failure_retains_first_asset(self):
        self.assertEqual(len(self.failure_retention(fail=".dmg")), 5)
        self.assertEqual(self.published["id"], 42)
        self.assertEqual(self.uploaded, [f"Frontmost-{self.version}.zip"])

    def test_malformed_release_response_retains_publication(self):
        self.assertEqual(len(self.failure_retention(malformed="release")), 3)
        self.assertEqual(self.published["id"], 42)
        self.assertEqual(self.uploaded, [])

    def test_malformed_upload_response_retains_accepted_asset(self):
        self.assertEqual(len(self.failure_retention(malformed="asset")), 4)
        self.assertEqual(self.published["id"], 42)
        self.assertEqual(self.uploaded, [f"Frontmost-{self.version}.zip"])

    def test_final_recheck_blocks_new_draft(self):
        paths = self.assets()
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", return_value=[]):
            release.preflight(self.sha)
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", return_value=[entry(self.tag, True)]) as api:
            with self.assertRaisesRegex(ValueError, "draft or published"):
                release.publish(self.sha, paths)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_final_later_page_errors_never_create_tag_or_release(self):
        paths = self.assets()
        for error in (subprocess.CalledProcessError(1, ["gh", "api", "404"]),
                      OSError("network"), {}, [dict(entry(), draft=None)]):
            with self.subTest(error=error), \
                    patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                    patch.object(release, "api", side_effect=[[entry()] * 100, error]) as api:
                with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)):
                    release.publish(self.sha, paths)
                self.assertEqual(api.call_count, 2)
                self.assertEqual(git(self.remote, "tag", "--list"), "")
                self.assertIsNone(self.published)

    def test_remote_tag_sha_verification_blocks_release(self):
        calls = []
        real_remote_ref = release.remote_ref
        def incorrect_verification(ref):
            value = real_remote_ref(ref)
            return "a" * 40 if value else None
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", side_effect=self.publication_api(calls)), \
                patch.object(release, "remote_ref", side_effect=incorrect_verification):
            with self.assertRaisesRegex(ValueError, "exact-SHA verification"):
                release.publish(self.sha, self.assets())
        self.assertEqual(len(calls), 2)
        self.assertIsNone(self.published)
        self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), self.sha)

    def test_missing_asset_fails_before_remote_mutation(self):
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api") as api:
            with self.assertRaises(ValueError):
                release.publish(self.sha, [Path(f"Frontmost-{self.version}.zip"),
                                           Path(f"Frontmost-{self.version}.dmg")])
        api.assert_not_called()
        self.assertEqual(git(self.remote, "tag", "--list"), "")


class WiringTests(unittest.TestCase):
    def test_manual_zero_input_immutable_gate_wiring(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertRegex(workflow, r"(?m)^name: Build$")
        self.assertEqual(workflow.split("on:\n", 1)[1].split("\npermissions:", 1)[0].strip(),
                         "workflow_dispatch:")
        self.assertEqual(workflow.count("uses: actions/checkout@v4"), 3)
        self.assertEqual(workflow.count("ref: ${{ github.sha }}"), 3)
        self.assertIn("group: frontmost-publication\n  cancel-in-progress: false", workflow)
        self.assertIn("needs: [version, build-windows]", workflow)
        self.assertIn("needs: version", workflow)
        self.assertEqual(workflow.count("GH_TOKEN: ${{ github.token }}"), 2)
        gates = [
            "preflight --sha", "build x64", "upload windows binaries", "download windows binaries",
            "import Developer ID certificate", "store notarytool credentials",
            "build macOS plugin (universal)", "verify universal binary",
            "merge Windows binaries into the bundle", "- name: codesign",
            "package, notarize & staple", "publish --sha",
        ]
        positions = [workflow.index(gate) for gate in gates]
        self.assertEqual(positions, sorted(positions))
        for required in ("PlatformToolset=v143", "WindowsTargetPlatformVersion=10.0",
                         'ARCHS="arm64 x86_64"', "CODE_SIGNING_ALLOWED=NO",
                         "codesign --verify --deep --strict", 'stapler staple "$DMG_PATH"',
                         'notarytool submit "$ZIP_PATH"', 'notarytool submit "$DMG_PATH"',
                         "APPLE_DEVELOPER_ID_CERTIFICATE", "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD",
                         "KEYCHAIN_PASSWORD", "NOTARYTOOL_APPLE_ID", "NOTARYTOOL_TEAM_ID",
                         "NOTARYTOOL_PASSWORD", "if-no-files-found: error",
                         "runs-on: windows-latest", "runs-on: macos-latest",
                         "PRODUCT_NAME: Frontmost", "BASE_DIR: test",
                         'ZIP_PATH="${PRODUCT}-${VERSION}.zip"',
                         'DMG_PATH="${PRODUCT}-${VERSION}.dmg"'):
            self.assertIn(required, workflow)
        for prohibited in ("continue-on-error", "always()", "inputs.", "softprops",
                           "needs.version.outputs.tag", "github.ref_name"):
            self.assertNotIn(prohibited, workflow)
        self.assertNotRegex(workflow, r"(?m)^\s+if:")

    def test_bump_and_no_destructive_publication(self):
        workflow = (ROOT / ".github/workflows/bump-version.yml").read_text()
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertIn("SELECTED_BRANCH: ${{ github.ref_name }}", workflow)
        self.assertIn('test "$REF_TYPE" = branch', workflow)
        for mode in ("patch", "minor", "major"):
            self.assertIn(f"- {mode}", workflow)
        helper = (ROOT / ".github/scripts/release.py").read_text()
        for prohibited in ('"DELETE"', '"PATCH"', "--clobber", "--force",
                           '"gh", "release", "create"', '"git", "tag"', "--force-with-lease"):
            self.assertNotIn(prohibited, helper)
        self.assertIn('"push.followTags=false"', helper)
        self.assertIn('"--no-follow-tags"', helper)
        self.assertNotIn("workflow_dispatch", helper)

    def test_publication_cli_accepts_assets_after_options(self):
        assets = [Path("Frontmost-1.2.3.zip"), Path("Frontmost-1.2.3.dmg")]
        with patch.object(sys, "argv", ["release.py", "publish", "--sha", "a" * 40,
                                       *map(str, assets)]), \
                patch.object(release, "publish") as publish:
            release.main()
        publish.assert_called_once_with("a" * 40, assets)


if __name__ == "__main__":
    unittest.main()
