"""Execute authorization and verify every credential-bearing dependency path."""

import os
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow(root, name):
    return yaml.safe_load((root / '.github/workflows' / name).read_text())


class OperationAuthorizationTests(unittest.TestCase):
    def test_actual_guard_event_ref_approval_matrix(self):
        guard = workflow(ROOT, 'authorize-operation.yml')['jobs']['authorize']['steps'][0]
        for event in ('push', 'pull_request', 'release', 'workflow_dispatch'):
            for ref in ('refs/heads/feature/op-499', 'refs/heads/release/v0.6.0', 'refs/heads/main', 'refs/tags/v0.6.0'):
                for approved in ('true', 'false', '', 'yes'):
                    with self.subTest(event=event, ref=ref, approved=approved):
                        result = subprocess.run(
                            ['bash', '-c', guard['run']],
                            env={**os.environ, 'REQUIRE_APPROVAL': 'true', 'APPROVED': approved,
                                 'CALLER_EVENT': event, 'CALLER_REF': ref},
                            capture_output=True, text=True, check=False,
                        )
                        allowed = event == 'workflow_dispatch' and ref == 'refs/heads/main' and approved == 'true'
                        self.assertEqual(result.returncode == 0, allowed)

    def test_existing_callers_keep_optional_contract(self):
        for name in ('release.yml', 'release-container.yml', 'container-release.yml', 'deploy-ansible.yml'):
            document = workflow(ROOT, name)
            inputs = document.get('on', document.get(True))['workflow_call']['inputs']
            self.assertFalse(inputs['require_approval']['default'])
            self.assertFalse(inputs['approved']['default'])
            self.assertFalse(inputs['require_approval']['required'])

    def test_all_publishers_and_deployer_depend_on_authorization(self):
        for name in ('release.yml', 'release-container.yml', 'container-release.yml', 'deploy-ansible.yml'):
            jobs = workflow(ROOT, name)['jobs']
            self.assertEqual(jobs['authorization']['uses'], './.github/workflows/authorize-operation.yml')
            for key in ('require_approval', 'approved'):
                self.assertEqual(jobs['authorization']['with'][key], '${{ inputs.' + key + ' }}')
            if 'validation' in jobs:
                self.assertEqual(jobs['validation']['needs'], 'authorization')
                self.assertEqual(jobs['release']['needs'], 'validation')
            else:
                job = jobs.get('publish', jobs.get('deploy'))
                self.assertEqual(job['needs'], 'authorization')
            for job in jobs.values():
                self.assertNotIn('always()', str(job.get('if', '')))

    def test_consumer_callers_require_both_independent_approvals(self):
        paths = os.environ.get('RECOVERY_GOVERNANCE_CHECKOUTS')
        if not paths:
            self.skipTest('Consumer matrix runs in the required provider verification contract')
        for path in paths.split(os.pathsep):
            root = Path(path)
            release = workflow(root, 'release.yml')
            triggers = release.get('on', release.get(True))
            self.assertEqual(set(triggers), {'workflow_dispatch', 'push'} if 'policy' in release['jobs'] else {'workflow_dispatch'})
            self.assertFalse(triggers['workflow_dispatch']['inputs']['release_approved']['default'])
            self.assertIn('release', release['jobs'])
            job = release['jobs']['release']
            self.assertIn("github.ref == 'refs/heads/main'", job['if'])
            self.assertIn("github.event_name == 'workflow_dispatch'", job['if'])
            self.assertIn('inputs.release_approved == true', job['if'])
            self.assertTrue(job['with']['require_approval'])
            self.assertEqual(job['with']['approved'], '${{ inputs.release_approved }}')
            self.assertEqual(job['with']['workflow_revision'], job['uses'].split('@')[-1])
            self.assertTrue(job['uses'].endswith('@feature/op-499-delivery-gates') or
                            len(job['uses'].split('@')[-1]) == 40)
            validation = workflow(root, 'validate.yml')
            validation_job = validation['jobs']['validation']
            self.assertEqual(validation_job['with']['workflow_revision'], validation_job['uses'].split('@')[-1])
            self.assertEqual(validation.get('on', validation.get(True))['push']['branches'], ['**'])
            self.assertNotIn('write', str(validation['permissions'].get('contents')))
            if (root / '.github/workflows/deploy.yml').exists():
                deploy = workflow(root, 'deploy.yml')
                triggers = deploy.get('on', deploy.get(True))
                self.assertEqual(set(triggers), {'workflow_call', 'workflow_dispatch'})
                for trigger in triggers.values():
                    self.assertFalse(trigger['inputs']['deployment_approved']['default'])
                for job in deploy['jobs'].values():
                    self.assertIn('inputs.deployment_approved == true', job['if'])
                    self.assertIn("github.ref == 'refs/heads/main'", job['if'])
                    self.assertIn("github.event_name == 'workflow_dispatch'", job['if'])
                job = next(job for job in deploy['jobs'].values() if 'deploy-ansible.yml@' in job['uses'])
                self.assertTrue(job['with']['require_approval'])
                self.assertEqual(job['with']['approved'], '${{ inputs.deployment_approved }}')
                rollback = workflow(root, 'rollback.yml')
                rollback_job = rollback['jobs']['rollback']
                self.assertIn('inputs.apply != true', rollback_job['if'])
                self.assertIn("github.event_name == 'workflow_dispatch'", rollback_job['if'])
                self.assertIn("github.ref == 'refs/heads/main'", rollback_job['if'])

    def test_helpers_bind_shared_revision_without_caller_revision(self):
        for name in ('release.yml', 'release-container.yml', 'container-release.yml', 'validate.yml'):
            document = workflow(ROOT, name)
            steps = next(job['steps'] for job in document['jobs'].values() if 'steps' in job)
            checkout = next(step for step in steps if 'WORKFLOW_SHA' in step.get('env', {}))
            self.assertEqual(checkout['env']['WORKFLOW_SHA'], '${{ inputs.workflow_revision }}')
            self.assertIn('"$RUNNER_TEMP/workflow-contract"', checkout['run'])
            self.assertNotIn('github.sha', checkout['run'])
            if name.startswith('release'):
                validation = document['jobs']['validation']
                self.assertEqual(validation['uses'], './.github/workflows/validate.yml')
                self.assertEqual(validation['with']['workflow_revision'], '${{ inputs.workflow_revision }}')

    def test_rollback_and_release_recovery_remain_available(self):
        release = workflow(ROOT, 'release.yml')
        self.assertIn('recovery_tag', release.get('on', release.get(True))['workflow_call']['inputs'])
        steps = workflow(ROOT, 'deploy-ansible.yml')['jobs']['deploy']['steps']
        rollback = next(step for step in steps if step.get('id') == 'rollback')
        self.assertIn('steps.apply.outcome', rollback['if'])
        self.assertIn('steps.health.outcome', rollback['if'])
        self.assertIn('git checkout --detach', rollback['run'])
        self.assertIn('PRIVILEGED_DEPLOY_COMMAND', rollback['run'])

    def test_automatic_guard_requires_main_push_and_policy(self):
        steps = workflow(ROOT, 'authorize-operation.yml')['jobs']['authorize']['steps']
        for event in ('push', 'pull_request', 'workflow_dispatch'):
            for ref in ('refs/heads/main', 'refs/heads/feature', 'refs/tags/v1.0.0'):
                for policy in ('', 'python3 scripts/policy.py'):
                    result = subprocess.run(['bash', '-c', steps[0]['run']], capture_output=True,
                                            env={**os.environ, 'AUTOMATIC': 'true', 'CALLER_EVENT': event,
                                                 'CALLER_REF': ref, 'POLICY_COMMAND': policy})
                    self.assertEqual(result.returncode == 0,
                                     event == 'push' and ref == 'refs/heads/main' and bool(policy))
        self.assertEqual(steps[1]['with']['ref'], '${{ inputs.revision || github.sha }}')
        policy = steps[2]
        result = subprocess.run(['bash', '-c', policy['run']], capture_output=True,
                                env={**os.environ, 'POLICY_COMMAND': 'exit 42'})
        self.assertEqual(result.returncode, 42)

    def test_automatic_recovery_never_falls_back_to_whole_target_apply(self):
        rollback = next(s for s in workflow(ROOT, 'deploy-ansible.yml')['jobs']['deploy']['steps']
                        if s.get('id') == 'rollback')
        guard = rollback['run'].split('git checkout')[0]
        self.assertIn('Automatic deployment requires scoped recovery.', guard)
        self.assertIn('exit 0', guard)
        for command in ('', 'exit 23'):
            result = subprocess.run(['bash', '-c', guard], capture_output=True,
                                    env={**os.environ, 'AUTOMATIC': 'true', 'PRIVILEGED_ROLLBACK_COMMAND': command})
            self.assertNotEqual(result.returncode, 0)

    def test_manual_scoped_recovery_does_not_apply_previous_whole_target(self):
        rollback = next(s for s in workflow(ROOT, 'deploy-ansible.yml')['jobs']['deploy']['steps']
                        if s.get('id') == 'rollback')
        guard = rollback['run'].split('git checkout')[0]
        for automatic in ('true', 'false'):
            result = subprocess.run(['bash', '-c', guard], capture_output=True, text=True,
                                    env={**os.environ, 'AUTOMATIC': automatic,
                                         'PRIVILEGED_ROLLBACK_COMMAND': 'printf scoped-recovery'})
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, 'scoped-recovery')
            failed = subprocess.run(['bash', '-c', guard], capture_output=True,
                                    env={**os.environ, 'AUTOMATIC': automatic,
                                         'PRIVILEGED_ROLLBACK_COMMAND': 'exit 23'})
            self.assertEqual(failed.returncode, 23)


if __name__ == '__main__':
    unittest.main()
