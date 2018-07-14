import argparse
import cgi
import datetime
import github
import os
import re
import requests
import shutil
import sys
import time

# Apparently there is no Traivis API python library that supports the latest API version (v3)
# There is travispy, it supports v2 (v2 is being phased out this (2018) year) and it doesn't
# allow to get the info we need, so we roll out our own awefully specific Travis API class.


class Travis:
    _headers = {
        'Travis-API-Version': '3',
        'User-Agent': 'ci-release-publisher',
    }

    def __init__(self, travis_token, travis_api_url):
        self._api_url = travis_api_url
        self._headers['Authorization'] = 'token {}'.format(travis_token)

    @classmethod
    def github_auth(cls, github_token, travis_api_url):
        # We have to use 2.1 API to get Travis-CI token off GitHub token,
        # see https://github.com/travis-ci/travis-ci/issues/9273.
        # API 2.1 is supposedly getting deprecaed sometime in 2018, so hopefully they will add
        # a similar endpoint to version 3 of the API. If not, we can always use the Travis API token,
        # it's just a bit less convenient.
        headers = {
            'Accept': 'application/vnd.travis-ci.2.1+json',
            'User-Agent': cls._headers['User-Agent'],
        }
        # API doc: https://docs.travis-ci.com/api/?http#with-a-github-token
        response = requests.post('{}/auth/github'.format(travis_api_url),
                                 headers=headers, params={'github_token': github_token})
        return Travis(response.json()['access_token'], travis_api_url)

    def branch_last_build_number(self, repo_slug, branch_name):
        _repo_slug = requests.utils.quote(repo_slug, safe='')
        _branch_name = requests.utils.quote(branch_name, safe='')
        # API doc: https://developer.travis-ci.com/resource/branch
        response = requests.get(
            '{}/repo/{}/branch/{}'.format(self._api_url, _repo_slug, _branch_name), headers=self._headers)
        return response.json()['last_build']['number']

    # Returns a list of build numbers of all builds that have not finished for a branch.
    # "not finished" bascially means that a build is active (queued/running) and it's not a restarted,
    # build as restarted builds have finished in the past during their first run.
    def branch_unfinished_build_numbers(self, repo_slug, branch_name):
        _repo_slug = requests.utils.quote(repo_slug, safe='')
        _branch_name = requests.utils.quote(branch_name, safe='')
        # There is no good way to request all builds for a branch, you can request only last 10 with
        # https://developer.travis-ci.com/resource/branch end point, which is not good enough, so we
        # just request all builds in general and filter by branch ourselves.
        build_numbers = []
        limit = 100
        offset = 0
        count = offset + 1  # just something to make the while condition true for the first run, as there is no do..while in python
        while offset < count:
            params = {
                # this will put all builds that have not finished yet first, their 'finished_at' is null
                'sort_by': 'finished_at:desc',
                'offset': offset,
                'limit': limit,
            }
            # API doc: https://developer.travis-ci.com/resource/builds
            response = requests.get('{}/repo/{}/builds'.format(self._api_url,
                                                               _repo_slug, _branch_name), headers=self._headers, params=params)
            json = response.json()
            offset += json['@pagination']['limit']
            count = json['@pagination']['count']
            branch_builds = [build for build in json['builds'] if build['branch']
                             ['name'] == branch_name and build['repository']['slug'] == repo_slug]
            build_numbers.extend(
                [build['number'] for build in branch_builds if build['finished_at'] == None])
            # If we find a finished build, then there is no point in looking at any further pages
            # as we are sorting them by finished builds - there would be no unfinished builds any further
            if any(build['finished_at'] != None for build in branch_builds):
                break
        print('DEBUG: Travis: found the following running build numbers: {}'.format(
            build_numbers))
        return build_numbers


class CIReleasePublisherError(Exception):
    pass


def download_artifact(github_token, src_url, dst_dir):
    # doc: https://developer.github.com/v3/repos/releases/#get-a-single-release-asset
    headers = {
        'Authorization': 'token {}'.format(github_token),
        'Accept': 'application/octet-stream',
    }
    r = requests.get(src_url, headers=headers, allow_redirects=True, stream=True)
    # Figure out filename
    cd = r.headers.get('Content-Disposition')
    filename = None
    if cd:
        _, params = cgi.parse_header(cd)
        if 'filename' in params:
            filename = params['filename']
    if not filename:
        filename = src_url.split('/')[-1]
    filepath = os.path.join(dst_dir, filename)
    with open(filepath, 'wb') as f:
        shutil.copyfileobj(r.raw, f)
    return filepath


def upload_artifacts(src_dir, release):
    print('Uploading artifacts to "{}" release'.format(release.tag_name))
    artifacts = sorted(os.listdir(src_dir))
    print('Found {} artifacts in "{}" directory'.format(len(artifacts), src_dir))
    for artifact in artifacts:
        artifact_path = os.path.join(src_dir, artifact)
        if os.path.isfile(artifact_path):
            print('\tStoring "{}" ({:.1f} MiB) artifact in the release...'.format(
                artifact, os.path.getsize(artifact_path)/1024/1024), end='', flush=True)
            start_time = time.time()
            release.upload_asset(artifact_path)
            elapsed_time = time.time() - start_time
            print(' Done in {:.2f} seconds'.format(elapsed_time))
    print('All artifacts for "{}" release are uploaded'.format(release.tag_name))


def download_artifcats(github_token, release, dst_dir):
    print('Downloading artifacts from "{}" release'.format(release.tag_name))
    assets = [asset for asset in release.get_assets()]
    print('Found {} artifacts in the release'.format(len(assets)))
    for asset in assets:
        print('\tDownloading artifact "{}" ({:.1f} MiB)...'.format(
            asset.name, asset.size/1024/1024), end='', flush=True)
        start_time = time.time()
        download_artifact(github_token, asset.url, dst_dir)
        elapsed_time = time.time() - start_time
        print(' Done in {:.2f} seconds'.format(elapsed_time))
    print('All artifacts from "{}" release are downloaded'.format(release.tag_name))


def store_artifacts(artifact_dir, release_name, release_body, github_token, github_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_job_number, travis_job_id):
    # Make sure no release with such tag name already exist
    releases = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(
        travis_repo_slug).get_releases()
    tag_name = 'ci-{}-{}-{}'.format(
        travis_branch, travis_build_number, travis_job_number)
    if any(release.tag_name == tag_name for release in releases):
        raise CIReleasePublisherError(
            'Release with tag name "{}" already exists. Was this job restarted? We don\'t support restarts.'.format(tag_name))
    # Create a draft release containing all the artifacts
    print('Creating a draft release with tag name "{}"'.format(tag_name))
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name,
        name=release_name if release_name else tag_name,
        message=release_body if release_body else
        ('Auto-generated temporary draft release containing build artifacts of [Travis-CI job #{}]({}/{}/jobs/{}).\n\n'
         'This release was created by `ci_release_publisher.py store` and will be automatically deleted by `ci_release_publisher.py cleanup` command,'
         'so in general you should never manually delete it, unless you don\'t use the script anymore.')
        .format(travis_job_id, travis_url, travis_repo_slug, travis_job_id),
        draft=True,
        prerelease=True,
        target_commitish=travis_commit)
    print('Release created')
    upload_artifacts(args.artifact_dir, release)


def stored_releases(releases, travis_branch, travis_build_number):
    prefix = 'ci-{}-{}-'.format(travis_branch, travis_build_number)
    releases_stored = [r for r in releases if r.draft and r.tag_name.startswith(
        prefix) and re.match('^\d+$', r.tag_name[len(prefix):])]
    releases_stored = sorted(releases_stored, key=lambda r: int(r.tag_name[len(prefix):]))
    return releases_stored


def collect_stored_artifacts(artifact_dir, github_token, github_api_url, travis_repo_slug, travis_branch, travis_build_number):
    releases = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(
        travis_repo_slug).get_releases()
    releases_stored = stored_releases(
        releases, travis_branch, travis_build_number)
    print('DEBUG: releases_stored = {}'.format(releases_stored))
    if not releases_stored:
        print(
            'Couldn\'t find any draft releases with stored build artifacts for this build')
        return
    for release in releases_stored:
        download_artifcats(github_token, release, artifact_dir)


def cleanup_draft_releases(github_token, github_api_url, travis_api_url, travis_repo_slug, travis_branch, travis_build_number):
    releases = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(
        travis_repo_slug).get_releases()
    print('Deleting unnecessary draft releases')
    prefix = 'ci-{}-'.format(travis_branch)
    branch_unfinished_build_numbers = Travis.github_auth(
        github_token, travis_api_url).branch_unfinished_build_numbers(travis_repo_slug, travis_branch)
    releases_stored_previous = [r for r in releases if r.draft and r.tag_name.startswith(prefix) and re.match('^\d+-\d+$', r.tag_name[len(prefix):]) and int(
        r.tag_name[len(prefix):].split('-')[0]) < int(travis_build_number) and int(r.tag_name[len(prefix):].split('-')[0]) not in branch_unfinished_build_numbers]
    releases_stored_previous = sorted(releases_stored_previous, key=lambda r: int(r.tag_name[len(prefix):].split('-')[1]))
    releases_stored_previous = sorted(releases_stored_previous, key=lambda r: int(r.tag_name[len(prefix):].split('-')[0]))
    for release in releases_stored_previous:
        print('Deleting draft release with tag name "{}"...'.format(
            release.tag_name), end='', flush=True)
        release.delete_release()
        print(' Done')
    for release in stored_releases(releases, travis_branch, travis_build_number):
        print('Deleting draft release with tag name "{}"...'.format(
            release.tag_name), end='', flush=True)
        release.delete_release()
        print(' Done')
    print('All unnecessary draft releases are deleted')


def publish_numbered_release(releases, artifact_dir, numbered_release_keep_count, numbered_release_keep_time, numbered_release_name, numbered_release_body, numbered_release_draft, numbered_release_prerelease, github_token, github_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_build_id):
    tag_name = 'ci-{}-{}'.format(travis_branch, travis_build_number)
    print('Starting the procedure of creating a numbered release with tag name "{}"'.format(tag_name))
    if any(release.tag_name == tag_name for release in releases):
        raise CIReleasePublisherError(
            'Release with tag name "{}" already exists. Was this job restarted? We don\'t support restarts'.format(tag_name))
    prefix = 'ci-{}-'.format(travis_branch)
    previous_numbered_releases = [r for r in releases if r.tag_name.startswith(prefix) and re.match(
        '^\d+$', r.tag_name[len(prefix):]) and int(r.tag_name[len(prefix):]) < int(travis_build_number)]
    previous_numbered_releases = sorted(previous_numbered_releases, key=lambda r: int(r.tag_name[len(prefix):]))
    if numbered_release_keep_count > 0:
        print('Keeping only {} numbered releases for "{}" branch.'.format(
            numbered_release_keep_count, travis_branch))
        extra_numbered_releases_to_remove = (
            len(previous_numbered_releases) + 1) - numbered_release_keep_count
        if extra_numbered_releases_to_remove < 0:
            extra_numbered_releases_to_remove = 0
        print('Found {} numbered releases for "{}" branch. Accounting for the one we are about to make, {} of existing numbered releases must be deleted.'.format(
            len(previous_numbered_releases), travis_branch, extra_numbered_releases_to_remove))
        for release in previous_numbered_releases[:extra_numbered_releases_to_remove]:
            print('Deleting release with tag name {}...'.format(release.tag_name), end='', flush=True)
            release.delete_release()
            if not release.draft:
                github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).get_git_ref('tags/{}'.format(release.tag_name)).delete()
            print(' Done')
        previous_numbered_releases = previous_numbered_releases[extra_numbered_releases_to_remove:]
    if numbered_release_keep_time > 0:
        expired_previous_numbered_releases = [r for r in previous_numbered_releases if (datetime.datetime.now() - r.created_at).total_seconds() > numbered_release_keep_time]
        print('Keeping only numbered releases that are not older than {} seconds for "{}" branch.'.format(
            numbered_release_keep_time, travis_branch))
        print('Found {} numbered releases for "{}" branch. {} of them will be deleted due to being too old.'.format(
            len(previous_numbered_releases), travis_branch, len(expired_previous_numbered_releases)))
        for release in expired_previous_numbered_releases:
            print('Deleting release with tag name {}...'.format(release.tag_name), end='', flush=True)
            release.delete_release()
            if not release.draft:
                github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).get_git_ref('tags/{}'.format(release.tag_name)).delete()
            print(' Done')
        previous_numbered_releases = [r for r in previous_numbered_releases if r not in expired_previous_numbered_releases]
    tag_name_tmp = '_{}'.format(tag_name)
    print('Creating a numbered draft release with tag name "{}"'.format(tag_name_tmp))
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name_tmp,
        name=numbered_release_name if numbered_release_name else
        'CI build of {} branch #{}'.format(
            travis_branch, travis_build_number),
        message=numbered_release_body if numbered_release_body else
        'This is an auto-generated release based on [Travis-CI build #{}]({}/{}/builds/{})'
        .format(travis_build_id, travis_url, travis_repo_slug, travis_build_id),
        draft=True,
        prerelease=numbered_release_prerelease,
        target_commitish=travis_commit)
    upload_artifacts(artifact_dir, release)
    print('Changing the tag name from "{}" to "{}"{}'.format(tag_name_tmp, tag_name, '' if numbered_release_draft else ' and removing the draft flag'))
    release.update_release(
        name=release.title, message=release.body, draft=numbered_release_draft, prerelease=numbered_release_prerelease, tag_name=tag_name)


def publish_latest_release(releases, artifact_dir, latest_release_name, latest_release_body, latest_release_draft, latest_release_prerelease, github_token, github_api_url, travis_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_build_id):
    tag_name = 'ci-{}-latest'.format(travis_branch)
    print('Starting the procedure of creating/updating a latest release with tag name "{}"'.format(tag_name))

    def there_is_a_newer_build_for_this_branch():
      if int(Travis.github_auth(github_token, travis_api_url).branch_last_build_number(travis_repo_slug, travis_branch)) != int(travis_build_number):
          print('Not creating/updating the "{}" release because there exists a newer build for "{}" branch on Travis-CI'.format(tag_name, travis_branch))
          print('We would either overwrite the artifacts uploaded by the newer build or mess up the release due to a race condition of both us updating the release at the same time')
          return True
      return False

    if there_is_a_newer_build_for_this_branch():
      return
    tag_name_tmp = '_{}'.format(tag_name)
    print('Creating a draft release with tag name "{}"'.format(tag_name_tmp))
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name_tmp,
        name=latest_release_name if latest_release_name else
        'Latest CI build of {} branch'.format(travis_branch),
        message=latest_release_body if latest_release_body else
        'This is an auto-generated release based on [Travis-CI build #{}]({}/{}/builds/{})'
        .format(travis_build_id, travis_url, travis_repo_slug, travis_build_id),
        draft=True,
        prerelease=latest_release_prerelease,
        target_commitish=travis_commit)
    upload_artifacts(artifact_dir, release)
    if there_is_a_newer_build_for_this_branch():
        print('Deleting the "{}" draft release'.format(tag_name_tmp))
        release.delete_release()
        return
    previous_release = [r for r in releases if r.tag_name == tag_name]
    if previous_release:
        print('Deleting the previous "{}" release'.format(tag_name))
        previous_release[0].delete_release()
        if not previous_release[0].draft:
            github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).get_git_ref('tags/{}'.format(previous_release[0].tag_name)).delete()
    print('Changing the tag name from "{}" to "{}"{}'.format(tag_name_tmp, tag_name, '' if latest_release_draft else ' and removing the draft flag'))
    release.update_release(
        name=release.title, message=release.body, draft=latest_release_draft, prerelease=latest_release_prerelease, tag_name=tag_name)


def publish_tag_release(releases, artifact_dir, tag_release_name, tag_release_body, tag_release_draft, tag_release_prerelease, github_token, github_api_url, travis_url, travis_repo_slug, travis_commit, travis_build_id, travis_tag):
    print('Starting the procedure of creating a tag release')
    if not travis_tag:
        print('No tag was pushed, skipping making a tag release')
        return
    print('Tag "{}" was pushed'.format(travis_tag))
    tag_name = travis_tag
    if any(release.tag_name == tag_name for release in releases):
        raise CIReleasePublisherError(
            'Release with tag name "{}" already exists. Was this job restarted? We don\'t support restarts'.format(tag_name))
    print('Creating a draft release with tag name "{}"'.format(tag_name))
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name,
        name=tag_release_name if tag_release_name else tag_name,
        message=tag_release_body if tag_release_body else
        'This is an auto-generated release based on [Travis-CI build #{}]({}/{}/builds/{})'
        .format(travis_build_id, travis_url, travis_repo_slug, travis_build_id),
        draft=True,
        prerelease=tag_release_prerelease,
        target_commitish=travis_commit)
    upload_artifacts(artifact_dir, release)
    if not tag_release_draft:
        print('Removing the draft flag from the "{}" release'.format(tag_name))
        release.update_release(
            name=release.title, message=release.body, draft=tag_release_draft, prerelease=tag_release_prerelease)


def publish_releases(artifact_dir, tag_release, tag_release_name, tag_release_body, tag_release_draft, tag_release_prerelease, latest_release, latest_release_name, latest_release_body, latest_release_draft, latest_release_prerelease, numbered_release, numbered_release_keep_count, numbered_release_keep_time, numbered_release_name, numbered_release_body, numbered_release_draft, numbered_release_prerelease, github_token, github_api_url, travis_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_build_id, travis_tag):
    if len(os.listdir(artifact_dir)) <= 0:
        raise CIReleasePublisherError('No artifacts were found in "{}" directory'.format(artifact_dir))
    releases = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(
        travis_repo_slug).get_releases()
    if numbered_release:
        publish_numbered_release(releases, artifact_dir, numbered_release_keep_count, numbered_release_keep_time, numbered_release_name,
                                 numbered_release_body, numbered_release_draft, numbered_release_prerelease, github_token, github_api_url, travis_url, travis_repo_slug,
                                 travis_branch, travis_commit, travis_build_number, travis_build_id)
    if latest_release:
        publish_latest_release(releases, artifact_dir, latest_release_name, latest_release_body, latest_release_draft, latest_release_prerelease, github_token,
                               github_api_url, travis_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit,
                               travis_build_number, travis_build_id)
    if tag_release:
        publish_tag_release(releases, artifact_dir, tag_release_name, tag_release_body, tag_release_draft, tag_release_prerelease, github_token, github_api_url,
                            travis_url, travis_repo_slug, travis_commit, travis_build_id, travis_tag)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='CI release publisher for GitHub using Travis-CI.')

    # Travis
    parser_travis = parser.add_mutually_exclusive_group()
    parser_travis.add_argument('--travis-public', dest='travis_type', action='store_const',
                            const='public', help='Use API of the free Travis-CI service, i.e. "https://travis-ci.org".')
    parser_travis.add_argument('--travis-private', dest='travis_type', action='store_const', const='private',
                            help='Use API of the paid Travis-CI service for GitHub private repositories, i.e. "https://travis-ci.com".')
    parser_travis.add_argument('--travis-enterprise', dest='travis_type', metavar='TRAVIS_URL', type=str,
                            help='Use API of Travis-CI running under a personal domain. Specify the actual URL, e.g. "http://travis.example.com".')
    parser.set_defaults(travis_type='public')

    parser.add_argument('--github-api-url', type=str, default="",
                        help='Use custom GitHib API URL, e.g. for self-hosted GitHub Enterprise instance. This should be an URL to the API endpoint, e.g. "https://api.github.com".')

    subparsers = parser.add_subparsers(dest='command')

    # store subparser
    parser_store = subparsers.add_parser(
        'store', help='Store job artifacts in a draft release for the later collection.')
    parser_store.add_argument('artifact_dir', metavar='artifact-dir',
                            help='Path to a direcotry containing artifacts that need to be stored.')
    parser_store.add_argument('--release-name', type=str,
                            help='Release name text.')
    parser_store.add_argument('--release-body', type=str,
                            help='Release body text.')

    # collect subparser
    parser_collect = subparsers.add_parser(
        'collect', help='Collect the previously stored build artifacts in a directory.')
    parser_collect.add_argument('artifact_dir', metavar='artifact-dir',
                                help='Path to a direcotry where artifacts should be collected to.')

    # cleanup subparser
    parser_collect = subparsers.add_parser(
        'cleanup', help='Delete all draft releases created by this and previous builds. Only the draft releases created by this script (i.e. following the tag name naming convention of this script) will be deleted.')

    # publsh subparser
    parser_publish = subparsers.add_parser(
        'publish', help='Publish a release with all artifacts from a directory.')
    parser_publish.add_argument('artifact_dir', metavar='artifact-dir',
                                help='Path to a direcotry containing build artifacts to publish.')

    # publsh subparser -- latest release
    parser_publish.add_argument('--latest-release', dest='latest_release', action='store_true',
                                    help='Publish latest release. The same "ci-$BRANCH-latest" tag release will be re-used (re-created) by each build.')
    parser_publish.set_defaults(latest_release=False)
    parser_publish.add_argument('--latest-release-name',
                                type=str, help='Release name text.')
    parser_publish.add_argument('--latest-release-body',
                                type=str, help='Release body text.')
    parser_publish.add_argument('--latest-release-draft', dest='latest_release_draft', action='store_true',
                                help='Publish as a draft.')
    parser_publish.set_defaults(latest_release_draft=False)
    parser_publish.add_argument('--latest-release-prerelease', dest='latest_release_prerelease', action='store_true',
                                help='Publish as a prerelease.')
    parser_publish.set_defaults(latest_release_prerelease=False)

    # publsh subparser -- numbered release
    parser_publish.add_argument('--numbered-release', dest='numbered_release', action='store_true',
                                        help='Publish a numbered release. A separate "ci-$BRANCH-$BUILD" tag release will be made for each build. You must specify at least one of --numbered-release-keep-* arguments specifying the strategy for keeping numbered builds.')
    parser_publish.set_defaults(numbered_release=False)
    parser_publish.add_argument('--numbered-release-keep-count', type=int,
                                default=0, help='Number of numbered releases to keep. If set to 0, this check is disabled, otherwise if the number of numbered releases exceeds that number, the oldest numbered release will be deleted. Due to a race condition of several Travis-CI builds running at the same time, although unlikely, it\'s possible for the numbered reeases to exceed that number by the number of concurrent Travis-CI builds running.')
    parser_publish.add_argument('--numbered-release-keep-time', type=int,
                                default=0, help='For how long to keep the numbered releases, in seconds. If set to 0, this check is disabled, otherwise all numbered releases that were made more than the specified amount of seconds in the past will be deleted.')
    parser_publish.add_argument(
        '--numbered-release-name', type=str, help='Release name text.')
    parser_publish.add_argument(
        '--numbered-release-body', type=str, help='Release body text.')
    parser_publish.add_argument('--numbered-release-draft', dest='numbered_release_draft', action='store_true',
                                help='Publish as a draft.')
    parser_publish.set_defaults(numbered_release_draft=False)
    parser_publish.add_argument('--numbered-release-prerelease', dest='numbered_release_prerelease', action='store_true',
                                help='Publish as a prerelease.')
    parser_publish.set_defaults(numbered_release_prerelease=False)

    # publsh subparser -- tag release
    parser_publish.add_argument('--tag-release', dest='tag_release', action='store_true',
                                    help='Publish a release for a pushed tag. A separate "$TAG" release will be made whenever a tag is pushed.')
    parser_publish.set_defaults(tag_release=False)
    parser_publish.add_argument('--tag-release-name',
                                type=str, help='Release name text.')
    parser_publish.add_argument('--tag-release-body',
                                type=str, help='Release body text.')
    parser_publish.add_argument('--tag-release-draft', dest='tag_release_draft', action='store_true',
                                help='Publish as a draft.')
    parser_publish.set_defaults(tag_release_draft=False)
    parser_publish.add_argument('--tag-release-prerelease', dest='tag_release_prerelease', action='store_true',
                                help='Publish as a prerelease.')
    parser_publish.set_defaults(tag_release_prerelease=False)

    args = parser.parse_args()

    # Sanity-check arguments

    travis_url = args.travis_type
    travis_api_url = '{}/api'.format(travis_url)
    if args.travis_type == 'public':
        travis_url = 'https://travis-ci.org'
        travis_api_url = 'https://api.travis-ci.org'
    elif args.travis_type == 'private':
        travis_url = 'https://travis-ci.com'
        travis_api_url = 'https://api.travis-ci.com'

    if not args.github_api_url:
        args.github_api_url = "https://api.github.com"

    def required_env(name):
        if name not in os.environ:
            raise CIReleasePublisherError('Required environment variable "{}" is not set'.format(name))
        return os.environ[name]


    def optional_env(name):
        if name not in os.environ:
            return None
        return os.environ[name]


    try:
        if args.command == 'store':
            if not os.path.isdir(args.artifact_dir):
                raise CIReleasePublisherError('Directory "{}" doesn\'t exist'.format(args.artifact_dir))
            store_artifacts(args.artifact_dir, args.release_name, args.release_body, required_env('GITHUB_ACCESS_TOKEN'),
                            args.github_api_url, travis_url, required_env('TRAVIS_REPO_SLUG'), required_env('TRAVIS_BRANCH'),
                            required_env('TRAVIS_COMMIT'), required_env('TRAVIS_BUILD_NUMBER'),
                            required_env('TRAVIS_JOB_NUMBER').split('.')[1], required_env('TRAVIS_JOB_ID'))
        elif args.command == 'collect':
            if not os.path.isdir(args.artifact_dir):
                raise CIReleasePublisherError('Directory "{}" doesn\'t exist'.format(args.artifact_dir))
            collect_stored_artifacts(args.artifact_dir, required_env('GITHUB_ACCESS_TOKEN'), args.github_api_url,
                                     required_env('TRAVIS_REPO_SLUG'), required_env('TRAVIS_BRANCH'),
                                     required_env('TRAVIS_BUILD_NUMBER'))
        elif args.command == 'cleanup':
            cleanup_draft_releases(required_env('GITHUB_ACCESS_TOKEN'), args.github_api_url, travis_api_url,
                                   required_env('TRAVIS_REPO_SLUG'), required_env('TRAVIS_BRANCH'),
                                   required_env('TRAVIS_BUILD_NUMBER'))
        elif args.command == 'publish':
            if not os.path.isdir(args.artifact_dir):
                raise CIReleasePublisherError('Directory "{}" doesn\'t exist'.format(args.artifact_dir))
            if not args.latest_release and not args.numbered_release and not args.tag_release:
                raise CIReleasePublisherError('You must specify what kind of release you would like to publish')
            if args.numbered_release:
                if args.numbered_release_keep_count < 0:
                    raise CIReleasePublisherError('--numbered-release-keep-count can\'t be set to a negative number')
                if args.numbered_release_keep_time < 0:
                    raise CIReleasePublisherError('--numbered-release-keep-time can\'t be set to a negative number')
                if args.numbered_release_keep_count == 0 and args.numbered_release_keep_time == 0:
                    raise CIReleasePublisherError('You must specify at least one of --numbered-release-keep-* options specifying the strategy for keeping numbered builds')
            publish_releases(args.artifact_dir, args.tag_release, args.tag_release_name, args.tag_release_body, args.tag_release_draft,
                             args.tag_release_prerelease, args.latest_release, args.latest_release_name, args.latest_release_body,
                             args.latest_release_draft, args.latest_release_prerelease, args.numbered_release,
                             args.numbered_release_keep_count, args.numbered_release_keep_time, args.numbered_release_name,
                             args.numbered_release_body, args.numbered_release_draft, args.numbered_release_prerelease,
                             required_env('GITHUB_ACCESS_TOKEN'), args.github_api_url, travis_api_url, travis_url,
                             required_env('TRAVIS_REPO_SLUG'), required_env('TRAVIS_BRANCH'), required_env('TRAVIS_COMMIT'),
                             required_env('TRAVIS_BUILD_NUMBER'), required_env('TRAVIS_BUILD_ID'), optional_env('TRAVIS_TAG'))
        else:
            raise CIReleasePublisherError('Specify one of "store", "collect", "cleanup" or "publish" commands.')
    except CIReleasePublisherError as e:
        print('Error: {}'.format(str(e)))
        sys.exit(1)

#+ remove by created_at
#+ change "continuous" tag prefx to "ci"
#+ tag releases
#- keep releases as drafts


#Allow setting number to 0 to disable it, allow setting time to 0 to disable it. At least one must be enabled. Both can be enabled, in which case it will remove both number above N and older T.

#No defaults. Error if no publishing method selected.
#ci-master-latest
#ci-master-123456789 <- max length
#continuous-master-uh

