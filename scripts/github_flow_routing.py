#!/usr/bin/env python3
"""Validate same-repository GitHub Flow PR provenance and current main ancestry."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tomllib


def route(root, event):
    project = tomllib.loads((root / 'pyproject.toml').read_text())
    capabilities = tomllib.loads((root / '.github/release.toml').read_text())
    if (project['tool']['wood']['branching'] != {'strategy': 'trunk'}
            or capabilities['branching'] != {'policy': 'infrastructure'}):
        raise ValueError('explicit_infrastructure_github_flow_required')
    repository = event['repository']['full_name']
    pr = event['pull_request']
    if (event['repository']['default_branch'] != 'main'
            or pr['base']['ref'] != 'main'
            or any(pr[side]['repo']['full_name'] != repository for side in ('head', 'base'))
            or not re.fullmatch(r'(feature|feat|fix|maintenance|chore|renovate)/[A-Za-z0-9][A-Za-z0-9._/-]*', pr['head']['ref'])):
        raise ValueError('invalid_github_flow_route')
    for side in ('head', 'base'):
        if not re.fullmatch(r'[0-9a-f]{40}', pr[side]['sha']):
            raise ValueError('invalid_revision')

    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()

    head, base = pr['head']['sha'], pr['base']['sha']
    if (git('rev-parse', 'HEAD') != head
            or git('rev-parse', 'refs/remotes/origin/main') != base
            or git('remote', 'get-url', 'origin') not in {
                f'https://github.com/{repository}', f'https://github.com/{repository}.git',
                f'git@github.com:{repository}.git'}):
        raise ValueError('checkout_revision_or_repository_mismatch')
    git('merge-base', '--is-ancestor', base, head)
    return {'strategy': 'trunk', 'target_branch': 'main', 'base_revision': base,
            'head_revision': head, 'repository': repository}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event-file', type=Path, required=True)
    parser.add_argument('--github-output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = route(Path.cwd(), json.loads(args.event_file.read_text()))
        args.github_output.write_text('incident=\napproval=\n')
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        print(json.dumps({'state': 'failed', 'reason': 'invalid_github_flow_route'}))
        return 1
    print(json.dumps({'state': 'passed', **result}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
