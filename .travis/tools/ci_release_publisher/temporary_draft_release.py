# -*- coding: utf-8 -*-

import github
import logging
import re

from . import config
from . import env
from . import github as github_helper
from . import travis
from .cleanup_store_scope import CleanupStoreScope
from .cleanup_store_release import CleanupStoreRelease

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

def _tag_name_tmp(travis_branch, travis_build_number, travis_job_number):
    return '{}{}'.format(config.tag_prefix_tmp, _tag_name(travis_branch, travis_build_number, travis_job_number))

def _break_tag_name_tmp(tag_name_tmp):
    if not tag_name_tmp.startswith(config.tag_prefix_tmp):
        return {'matched': False}
    tag_name = tag_name_tmp[len(config.tag_prefix_tmp):]
    return _break_tag_name(tag_name)

def args(parser):
    parser.add_argument('--release-name', type=str, help='Release name text. If not specified a predefined text is used.')
    parser.add_argument('--release-body', type=str, help='Release body text. If not specified a predefined text is used.')

def publish_validate_args(args):
    return True

def publish_with_args(args, releases, artifact_dir, github_api_url, travis_api_url, travis_url):
    publish(releases, artifact_dir, args.release_name, args.release_body, github_api_url, travis_url)

def publish(releases, artifact_dir, release_name, release_body, github_api_url, travis_url):
    github_token        = env.required('GITHUB_ACCESS_TOKEN')
    travis_repo_slug    = env.required('TRAVIS_REPO_SLUG')
    travis_branch       = env.required('TRAVIS_BRANCH')
    travis_commit       = env.required('TRAVIS_COMMIT')
    travis_build_number = env.required('TRAVIS_BUILD_NUMBER')
    travis_job_number   = env.required('TRAVIS_JOB_NUMBER').split('.')[1]
    travis_job_id       = env.required('TRAVIS_JOB_ID')

    tag_name = _tag_name(travis_branch, travis_build_number, travis_job_number)
    logging.info('* Starting the procedure of creating a temporary draft release with tag name "{}".'.format(tag_name))
    tag_name_tmp = _tag_name_tmp(travis_branch, travis_build_number, travis_job_number)
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name_tmp,
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
    github_helper.upload_artifacts(artifact_dir, release)
    logging.info('Changing the tag name from "{}" to "{}".'.format(tag_name_tmp, tag_name))
    release.update_release(name=release.title, message=release.body, draft=True, prerelease=True, tag_name=tag_name)

def cleanup_store(releases, scopes, release_kinds, on_nonallowed_failure, github_api_url, travis_api_url):
    github_token         = env.required('GITHUB_ACCESS_TOKEN')
    travis_repo_slug     = env.required('TRAVIS_REPO_SLUG')
    travis_branch        = env.required('TRAVIS_BRANCH')
    travis_build_number  = env.required('TRAVIS_BUILD_NUMBER')
    travis_build_id      = env.required('TRAVIS_BUILD_ID')
    travis_job_number    = env.required('TRAVIS_JOB_NUMBER').split('.')[1]
    travis_test_result   = env.optional('TRAVIS_TEST_RESULT')
    travis_allow_failure = env.optional('TRAVIS_ALLOW_FAILURE')

    logging.info('* Deleting existing temporary draft releases".')

    if on_nonallowed_failure:
        has_nonallowed_failure = travis_test_result == '1' and travis_allow_failure == 'false'
        if not has_nonallowed_failure:
            has_nonallowed_failure = travis.Travis.github_auth(github_token, travis_api_url).build_has_failed_nonallowfailure_job(travis_build_id)
        if not has_nonallowed_failure:
            logging.info('Current build has no jobs that both have failed and have no allow_failure set.')
            return

    branch_unfinished_build_numbers = []
    if CleanupStoreScope.PREVIOUS_FINISHED_BUILDS in scopes:
        branch_unfinished_build_numbers = travis.Travis.github_auth(github_token, travis_api_url).branch_unfinished_build_numbers(travis_repo_slug, travis_branch)

    def should_delete(r):
        if not r.draft:
            return False

        info = None
        if not info and CleanupStoreRelease.COMPLETE in release_kinds:
            _info = _break_tag_name(r.tag_name)
            if _info['matched']:
                info = _info
        if not info and CleanupStoreRelease.INCOMPLETE in release_kinds:
            _info = _break_tag_name_tmp(r.tag_name)
            if _info['matched']:
                info = _info

        if not info:
            return False

        if not info['branch'] == travis_branch:
            return False

        result = False
        if not result and CleanupStoreScope.CURRENT_JOB in scopes:
            result = int(info['build_number']) == int(travis_build_number) and int(info['job_number']) == int(travis_job_number)
        if not result and CleanupStoreScope.CURRENT_BUILD in scopes:
            result = int(info['build_number']) == int(travis_build_number)
        if not result and CleanupStoreScope.PREVIOUS_FINISHED_BUILDS in scopes:
            result = int(info['build_number']) < int(travis_build_number) and info['build_number'] not in branch_unfinished_build_numbers
        return result

    releases_to_delete = [r for r in releases if should_delete(r)]

    releases_to_delete = sorted(releases_to_delete, key=lambda r: _break_tag_name(r.tag_name)['matched'])
    releases_to_delete = sorted(releases_to_delete, key=lambda r: int(_break_tag_name(r.tag_name)['job_number'] if _break_tag_name(r.tag_name)['matched'] else _break_tag_name_tmp(r.tag_name)['job_number']))
    releases_to_delete = sorted(releases_to_delete, key=lambda r: int(_break_tag_name(r.tag_name)['build_number'] if _break_tag_name(r.tag_name)['matched'] else _break_tag_name_tmp(r.tag_name)['build_number']))

    for release in releases_to_delete:
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
