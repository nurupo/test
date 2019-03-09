# -*- coding: utf-8 -*-

import github
import re

from . import config
from . import env
from . import github as github_helper
from . import travis

_tmp_tag_suffix = 'tag'

def _tag_name(travis_tag):
    return '{}'.format(travis_tag)

def _break_tag_name(tag_name):
    return {'matched': True, 'tag': tag_name}

def _tag_name_tmp(travis_tag):
    return '{}{}-{}-{}'.format(config.tmp_tag_prefix, config.tag_prefix, _tag_name(travis_tag), _tmp_tag_suffix)

def _break_tag_name_tmp(tag_name_tmp):
    prefix = '{}{}'.format(config.tmp_tag_prefix, config.tag_prefix)
    if not tag_name.startswith(prefix) or not tag_name.endswith(_tmp_tag_suffix):
        return {'matched': False}
    tag_name = tag_name[len(prefix):-len(_tmp_tag_suffix)]
    if not tag_name.startswith('-') or not tag_name.endswith('-'):
        return {'matched': False}
    tag_name = tag_name[1:-1]
    return _break_tag_name(tag_name)

def publish_args(parser):
    parser.add_argument('--tag-release', dest='tag_release', action='store_true',
                        help='Publish a release for a pushed tag. A separate "<tag>" release will be made whenever a tag is pushed.')
    parser.set_defaults(tag_release=False)
    parser.add_argument('--tag-release-name', type=str, help='Release name text.  If not specified a predefined text is used.')
    parser.add_argument('--tag-release-body', type=str, help='Release body text.  If not specified a predefined text is used.')
    parser.add_argument('--tag-release-draft', dest='tag_release_draft', action='store_true', help='Publish as a draft.')
    parser.set_defaults(tag_release_draft=False)
    parser.add_argument('--tag-release-prerelease', dest='tag_release_prerelease', action='store_true', help='Publish as a prerelease.')
    parser.set_defaults(tag_release_prerelease=False)

def publish_validate_args(args):
    return args.tag_release

def publish_with_args(args, releases, artifact_dir, github_api_url, travis_api_url, travis_url):
    if not args.tag_release:
        return
    publish(releases, artifact_dir, args.tag_release_name, args.tag_release_body, args.tag_release_draft, args.tag_release_prerelease,
            github_api_url, travis_url)

def cleanup(releases, branch_unfinished_build_numbers, github_api_url):
    github_token        = env.required('GITHUB_ACCESS_TOKEN')
    travis_repo_slug    = env.required('TRAVIS_REPO_SLUG')
    travis_build_number = env.required('TRAVIS_BUILD_NUMBER')
    travis_tag          = env.optional('TRAVIS_TAG')

    if not travis_tag:
        return
    logging.info('* Deleting draft releases left over by previous tag releases due to their builds failing or being cancelled.')
    # FIXME(nurupo): once Python 3.8 is out, use Assignemnt Expression to prevent expensive _break_tag_name() calls https://www.python.org/dev/peps/pep-0572/
    previous_tag_releases_tmp = [r for r in releases if r.draft and _break_tag_name_tmp(r.tag_name)['matched'] and _break_tag_name_tmp(r.tag_name)['tag'] == travis_tag]
    if not previous_tag_releases_tmp or any(n != travis_build_number for n in branch_unfinished_build_numbers):
        return
    for r in previous_tag_releases_tmp:
        try:
            github_helper.delete_release_with_tag(r, github_token, github_api_url, travis_repo_slug)
        except Exception as e:
            logging.exception('Error: {}'.format(str(e)))

def publish(releases, artifact_dir, tag_release_name, tag_release_body, tag_release_draft, tag_release_prerelease, github_api_url, travis_url):
    github_token        = env.required('GITHUB_ACCESS_TOKEN')
    travis_repo_slug    = env.required('TRAVIS_REPO_SLUG')
    travis_commit       = env.required('TRAVIS_COMMIT')
    travis_build_number = env.required('TRAVIS_BUILD_NUMBER')
    travis_build_id     = env.required('TRAVIS_BUILD_ID')
    travis_tag          = env.optional('TRAVIS_TAG')

    if not travis_tag:
        return
    tag_name = _tag_name(travis_tag)
    logging.info('* Starting the procedure of creating a tag release with tag name "{}".'.format(tag_name))

    def _is_latest_build_for_branch():
        if int(Travis.github_auth(github_token, travis_api_url).branch_last_build_number(travis_repo_slug, travis_tag)) == int(travis_build_number):
            return True
        logging.info('Not creating the "{}" release because there is a newer build for "{}" tag running on Travis-CI.'.format(tag_name, travis_tag))
        return False

    if not _is_latest_build_for_branch():
        return
    tag_name_tmp = _tag_name_tmp(travis_tag)
    logging.info('Creating a draft release with tag name "{}".'.format(tag_name_tmp))
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name_tmp,
        name=tag_release_name if tag_release_name else tag_name,
        message=tag_release_body if tag_release_body else
                'This is an auto-generated release based on [Travis-CI build #{}]({}/{}/builds/{})'
                .format(travis_build_id, travis_url, travis_repo_slug, travis_build_id),
        draft=True,
        prerelease=tag_release_prerelease,
        target_commitish=travis_commit)
    github_helper.upload_artifacts(artifact_dir, release)
    if not _is_latest_build_for_branch():
        github_helper.delete_release_with_tag(release, github_token, github_api_url, travis_repo_slug)
        return
    previous_release = [r for r in releases if r.tag_name == tag_name]
    if previous_release:
        # Delete release but keep the tag, since in Tag Releases the user specifies the tag and it was just pushed
        logging.info('Deleting release with tag name "{}".'.format(tag_name))
        previous_release[0].delete_release()
    logging.info('Changing the tag name from "{}" to "{}"{}.'.format(tag_name_tmp, tag_name, '' if tag_release_draft else ' and removing the draft flag'))
    release.update_release(name=release.title, message=release.body, draft=tag_release_draft, prerelease=tag_release_prerelease, tag_name=tag_name)
