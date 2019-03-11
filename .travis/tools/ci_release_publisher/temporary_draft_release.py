# -*- coding: utf-8 -*-

import github
import logging
import re

from . import config
from . import env
from . import github as github_helper
from . import travis

_tag_suffix = 'tmp'

def _tag_name(travis_branch, travis_build_number, travis_job_number):
    return '{}-{}-{}-{}-{}'.format(config.tag_prefix, travis_branch, travis_build_number, travis_job_number, _tag_suffix)

def _break_tag_name(tag_name):
    if not tag_name.startswith(config.tag_prefix) or not tag_name.endswith(_tag_suffix):
        return {'matched': False}
    tag_name = tag_name[len(config.tag_prefix):-len(_tag_suffix)]
    m = re.match('^-(?P<branch>.*)-(?P<build_number>\d+)-(?P<job_number>\d+)-$', tag_name)
    if not m:
        return {'matched': False}
    return {'matched': True, 'branch': m.group('branch'), 'build_number': m.group('build_number'), 'job_number': m.group('job_number')}

def args(parser):
    parser.add_argument('--release-name', type=str, help='Release name text. If not specified a predefined text is used.')
    parser.add_argument('--release-body', type=str, help='Release body text. If not specified a predefined text is used.')

def publish_validate_args(args):
    return True

def publish_with_args(args, artifact_dir, github_api_url, travis_api_url, travis_url):
    publish(artifact_dir, args.release_name, args.release_body, github_api_url, travis_url)

def publish(artifact_dir, release_name, release_body, github_api_url, travis_url):
    github_token        = env.required('GITHUB_ACCESS_TOKEN')
    travis_repo_slug    = env.required('TRAVIS_REPO_SLUG')
    travis_branch       = env.required('TRAVIS_BRANCH')
    travis_commit       = env.required('TRAVIS_COMMIT')
    travis_build_number = env.required('TRAVIS_BUILD_NUMBER')
    travis_job_number   = env.required('TRAVIS_JOB_NUMBER').split('.')[1]
    travis_job_id       = env.required('TRAVIS_JOB_ID')

    tag_name = _tag_name(travis_branch, travis_build_number, travis_job_number)
    logging.info('* Creating a draft release with tag name "{}".'.format(tag_name))
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name,
        name=release_name if release_name else
             'Temporary draft release {}'
             .format(tag_name),
        message=release_body if release_body else
                ('Auto-generated temporary draft release containing build artifacts of [Travis-CI job #{}]({}/{}/jobs/{}).\n\n'
                'This release was created by `ci_release_publisher.py store` and will be automatically deleted by `ci_release_publisher.py cleanup` command, '
                'so in general you should never manually delete it, unless you don\'t use the `ci_release_publisher.py` script anymore.')
                .format(travis_job_id, travis_url, travis_repo_slug, travis_job_id),
        draft=True,
        prerelease=True,
        target_commitish=travis_commit)
    logging.info('Release created.')
    github_helper.upload_artifacts(artifact_dir, release)

def cleanup(releases, branch_unfinished_build_numbers, github_api_url):
    github_token        = env.required('GITHUB_ACCESS_TOKEN')
    travis_repo_slug    = env.required('TRAVIS_REPO_SLUG')
    travis_branch       = env.required('TRAVIS_BRANCH')
    travis_build_number = env.required('TRAVIS_BUILD_NUMBER')
    travis_tag          = env.optional('TRAVIS_TAG')

    logging.info('* Deleting temporary draft releases created to store per-job artifacts.')
    # When a tag is pushed, we create ci-<tag>-<build_number>-<job_number> releases
    # When no tag is pushed, we create ci-<branch_name>-<build_number>-<job_number> releases
    # FIXME(nurupo): what does that mean? ^
    print(travis_branch)
    print(travis_tag)
    print(branch_unfinished_build_numbers)
    travis_branch = travis_branch if not travis_tag else travis_tag
    # FIXME(nurupo): once Python 3.8 is out, use Assignemnt Expression to prevent expensive _break_tag_name() calls https://www.python.org/dev/peps/pep-0572/
    releases_stored_previous = [r for r in releases if r.draft and _break_tag_name(r.tag_name)['matched'] and _break_tag_name(r.tag_name)['branch'] == travis_branch and
                               ( (int(_break_tag_name(r.tag_name)['build_number']) == int(travis_build_number)) or ( (int(_break_tag_name(r.tag_name)['build_number']) < int(travis_build_number)) and (_break_tag_name(r.tag_name)['build_number'] not in branch_unfinished_build_numbers) ) )]
    releases_stored_previous = sorted(releases_stored_previous, key=lambda r: int(_break_tag_name(r.tag_name)['job_number']))
    releases_stored_previous = sorted(releases_stored_previous, key=lambda r: int(_break_tag_name(r.tag_name)['build_number']))
    print(r)
    print(releases_stored_previous)
    for release in releases_stored_previous:
        try:
            github_helper.delete_release_with_tag(release, github_token, github_api_url, travis_repo_slug)
        except Exception as e:
            logging.exception('Error: {}'.format(str(e)))

def download(releases, artifact_dir):
    github_token        = env.required('GITHUB_ACCESS_TOKEN')
    travis_repo_slug    = env.required('TRAVIS_REPO_SLUG')
    travis_branch       = env.required('TRAVIS_BRANCH')
    travis_build_number = env.required('TRAVIS_BUILD_NUMBER')

    # FIXME(nurupo): once Python 3.8 is out, use Assignemnt Expression to prevent expensive _break_tag_name() calls https://www.python.org/dev/peps/pep-0572/
    releases_stored = [r for r in releases if r.draft and _break_tag_name(r.tag_name)['matched'] and _break_tag_name(r.tag_name)['branch'] == travis_branch and int(_break_tag_name(r.tag_name)['build_number']) == int(travis_build_number)]
    releases_stored = sorted(releases_stored, key=lambda r: int(_break_tag_name(r.tag_name)['job_number']))
    if not releases_stored:
        logging.info('Couldn\'t find any draft releases with stored build artifacts for this build.')
        return
    for release in releases_stored:
        github_helper.download_artifcats(github_token, release, artifact_dir)
