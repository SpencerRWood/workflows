"""GitHub Flow routing preserves provenance and ancestry without promotion gates."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('flow', Path(__file__).parents[1] / 'scripts/github_flow_routing.py')
FLOW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FLOW)


class GitHubFlowRoutingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', 'https://github.com/owner/repo.git')
        (self.root / '.github').mkdir()
        (self.root / 'pyproject.toml').write_text('[tool.wood.branching]\nstrategy="trunk"\n')
        (self.root / '.github/release.toml').write_text('[branching]\npolicy="infrastructure"\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'test: base')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.git('checkout', '-qb', 'feature/example')
        self.git('commit', '--allow-empty', '-qm', 'test: feature')
        self.head = self.git('rev-parse', 'HEAD')
        self.event = {'repository': {'full_name': 'owner/repo', 'default_branch': 'main'},
                      'pull_request': {side: {'ref': ref, 'sha': sha, 'repo': {'full_name': 'owner/repo'}}
                                       for side, ref, sha in [('base', 'main', self.base),
                                                              ('head', 'feature/example', self.head)]}}

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True, stderr=subprocess.DEVNULL).strip()

    def test_supported_short_lived_branches_target_current_main(self):
        for prefix in ('feature', 'feat', 'fix', 'maintenance', 'chore', 'renovate'):
            self.event['pull_request']['head']['ref'] = prefix + '/example'
            result = FLOW.route(self.root, self.event)
            self.assertEqual(result['head_revision'], self.head)
            self.assertEqual(result['base_revision'], self.base)

    def test_forks_wrong_targets_permanent_heads_and_false_revisions_fail(self):
        for side, key, value in [('base', 'ref', 'development'), ('head', 'ref', 'development'),
                                 ('head', 'ref', 'main'), ('head', 'sha', 'a' * 40),
                                 ('base', 'sha', 'b' * 40), ('head', 'sha', '--help')]:
            event = copy.deepcopy(self.event)
            event['pull_request'][side][key] = value
            with self.subTest(side=side, key=key, value=value), self.assertRaises(ValueError):
                FLOW.route(self.root, event)
        self.event['pull_request']['head']['repo']['full_name'] = 'fork/repo'
        with self.assertRaises(ValueError):
            FLOW.route(self.root, self.event)

    def test_main_must_be_ancestor_and_application_policy_is_not_migrated(self):
        self.git('checkout', '-q', 'main')
        self.git('commit', '--allow-empty', '-qm', 'test: new main')
        base = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', base)
        self.git('checkout', '-q', 'feature/example')
        self.event['pull_request']['base']['sha'] = base
        with self.assertRaises(subprocess.CalledProcessError):
            FLOW.route(self.root, self.event)
        (self.root / 'pyproject.toml').write_text('[tool.wood.branching]\nstrategy="integration"\n')
        with self.assertRaises(ValueError):
            FLOW.route(self.root, self.event)
