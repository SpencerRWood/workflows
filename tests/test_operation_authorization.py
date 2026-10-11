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
            self.assertEqual(jobs['authorization']['with'], {
                'require_approval': '${{ inputs.require_approval }}', 'approved': '${{ inputs.approved }}'})
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
            self.assertEqual(set(triggers), {'workflow_dispatch'})
            self.assertFalse(triggers['workflow_dispatch']['inputs']['release_approved']['default'])
            self.assertEqual(set(release['jobs']), {'release'})
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


if __name__ == '__main__':
    unittest.main()
