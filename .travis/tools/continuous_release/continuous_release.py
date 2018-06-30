import argparse
import cgi
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
        'User-Agent': 'continuous-release-publisher',
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
            '{}/repo/{}/branch/{}'.format(self._api_url, _repo_slug, _branch_name), headers=headers)
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


class ContinuousReleaseError(Exception):
    pass


def download_file(src_url, dst_dir):
    r = requests.get(src_url, allow_redirects=True, stream=True)
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
        shutil.copyfileobj(r.content, f)
    return filepath


def upload_artifacts(src_dir, release):
    print('Uploading artifacts to "{}" release\n'.format(release.tag_name))
    artifacts = sorted(os.listdir(src_dir))
    print('Found {} artifacts in "{}" directory\n'.format(len(artifacts), src_dir))
    for artifact in artifacts:
        artifact_path = os.path.join(src_dir, artifact)
        if os.path.isfile(artifact_path):
            print('\tStoring "{}" ({:.1f} MiB) artifact in the release...'.format(
                artifact, os.path.getsize(artifact_path)/1024/1024))
            start_time = time.time()
            release.upload_asset(artifact_path)
            elapsed_time = time.time() - start_time
            print(' Done in {:.2f} seconds\n'.format(elapsed_time))
    print('All artifacts for "{}" release are uploaded\n'.format(release.tag_name))


def download_artifcats(release, dst_dir):
    print('Downloading artifacts from "{}" release\n'.format(release.tag_name))
    assets = [asset for asset in release.get_assets()]
    print('Found {} artifacts in the release\n'.format(len(assets)))
    for asset in assets:
        print('\tDownloading artifact "{}" ({:.1f} MiB)...'.format(
            asset.name, asset.size/1024/1024))
        start_time = time.time()
        download_file(asset.browser_download_url, dst_dir)
        elapsed_time = time.time() - start_time
        print(' Done in {:.2f} seconds\n'.format(elapsed_time))
    print('All artifacts from "{}" release are downloaded\n'.format(release.tag_name))


def store_artifacts(artifact_dir, release_name, release_body, github_token, github_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_job_number, travis_job_id):
    # Make sure no release with such tag name already exist
    releases = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(
        travis_repo_slug).get_releases()
    tag_name = 'continuous-{}-{}-{}'.format(
        travis_branch, travis_build_number, travis_job_number)
    if any(release.tag_name == tag_name for release in releases):
        raise ContinuousReleaseError(
            'Release with tag name "{}" already exists. Was this job restarted? We don\'t support restarts.'.format(tag_name))
    # Create a draft release containing all the artifacts
    print('Creating a draft release with tag name "{}"\n'.format(tag_name))
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name,
        name=release_name if release_name else tag_name,
        message=release_body if release_body else
        ('Auto-generated temporary draft release containing build artifacts of [Travis-CI job #{}]({}/{}/jobs/{}).\n\n'
         'This release was created by `continuous_release_publisher.py store` and will be automatically deleted by `continuous_release_publisher.py pubish` command,'
         'so in general you should never manually delete it, unless you don\'t use the script anymore.')
        .format(travis_job_id, travis_url, travis_repo_slug, travis_job_id),
        draft=True,
        prerelease=True,
        target_commitish=travis_commit)
    print('Release created\n')
    upload_artifacts(args.artifact_dir, release)


def stored_releases(releases, travis_branch, travis_build_number):
    prefix = 'continuous-{}-{}-'.format(travis_branch, travis_build_number)
    releases_stored = [r for r in releases if r.draft and r.tag_name.startswith(
        prefix) and re.match('^\d+$', r.tag_name[len(prefix):])]
    releases_stored = sorted(releases_stored, key=lambda r: r.tag_name[len(prefix):])
    return releases_stored


def collect_stored_artifacts(artifact_dir, github_token, github_api_url, travis_repo_slug, travis_branch, travis_build_number):
    releases = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(
        travis_repo_slug).get_releases()
    releases_stored = stored_releases(
        releases, travis_branch, travis_build_number)
    print('DEBUG: releases_stored = {}\n'.format(releases_stored))
    if not releases_stored:
        print(
            'Couldn\'t find any draft releases with stored build artifacts for this build')
        return
    for release in releases_stored:
        download_artifcats(release, artifact_dir)


def publish_numbered_release(numbered_release_count, releases, artifact_dir, numbered_release_name, numbered_release_body, github_token, github_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_build_id):
    tag_name = 'continuous-{}-{}'.format(travis_branch, travis_build_number)
    print('Starting the procedure of creating a numbered release with tag name "{}"\n'.format(tag_name))
    if any(release.tag_name == tag_name for release in releases):
        raise ContinuousReleaseError(
            'Release with tag name "{}" already exists. Was this job restarted? We don\'t support restarts'.format(tag_name))
    print('Keeping only {} numbered releases for "{}" branch.\n'.format(
        numbered_release_count, travis_branch))
    prefix = 'continuous-{}-'.format(travis_branch)
    previous_numbered_releases = [r for r in releases if not r.draft and r.tag_name.startswith(prefix) and re.match(
        '^\d+$', r.tag_name[len(prefix):]) and int(r.tag_name[len(prefix):]) < int(travis_build_number)]
    extra_numbered_releases_to_remove = (
        len(previous_numbered_releases) + 1) - numbered_release_count
    if extra_numbered_releases_to_remove < 0:
        extra_numbered_releases_to_remove = 0
    print('Found {} numbered releases for "{}" branch. Accounting for the one we are about to make, {} of existing numbered releases must be deleted.\n'.format(
        len(previous_numbered_releases), travis_branch, extra_numbered_releases_to_remove))
    for release in previous_numbered_releases[-extra_numbered_releases_to_remove:]:
        print('Deleting release with tag name {}...'.format(release.tag_name))
        release.delete_release()
        print(' Done\n')
    print('Creating a numbered draft release with tag name "{}"\n'.format(tag_name))
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name,
        name=numbered_release_name if numbered_release_name else
        'Continuous build of {} branch #{}'.format(
            travis_branch, travis_build_number),
        message=numbered_release_body if numbered_release_body else
        'This is an auto-generated release based on [Travis-CI build #{}]({}/{}/builds/{})'
        .format(travis_build_id, travis_url, travis_repo_slug, travis_build_id),
        draft=True,
        prerelease=True,
        target_commitish=travis_commit)
    upload_artifacts(artifact_dir, release)
    print('Removing the draft flag from the "{}" release\n'.format(tag_name))
    release.update_release(
        name=release.name, message=release.message, draft=False, prerelease=True)


def publish_latest_release(releases, artifact_dir, latest_release_name, latest_release_body, github_token, github_api_url, travis_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_build_id):
    tag_name = 'continuous-{}'.format(travis_branch)
    print('Starting the procedure of creating/updating a latest release with tag name "{}"\n'.format(tag_name))
    if int(Travis.github_auth(github_token, travis_api_url).branch_last_build_number(travis_repo_slug, travis_branch)) != int(travis_build_number):
        print('Not creating/updating the "{}" release because there exists a newer build for "{}" branch on Travis-CI.\n'.format(tag_name, travis_branch))
        print('We would either overwrite the artifacts uploaded by the newer build or mess up the release due to a race condition of both us updating the release at the same time.\n')
        return
    previous_release = [r for r in releases if r.tag_name == tag_name]
    if previous_release:
        print('Deleting the previous "{}" release\n'.format(tag_name))
        previous_release[0].delete_release()
    print('Creating a draft release with tag name "{}"\n'.format(tag_name))
    release = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).create_git_release(
        tag=tag_name,
        name=latest_release_name if latest_release_name else
        'Continuous build of {} branch'.format(os.environ['TRAVIS_BRANCH']),
        message=latest_release_body if latest_release_body else
        'This is an auto-generated release based on [Travis-CI build #{}]({}/{}/builds/{})'
        .format(travis_build_id, travis_url, travis_repo_slug, travis_build_id),
        draft=True,
        prerelease=True,
        target_commitish=travis_commit)
    upload_artifacts(artifact_dir, release)
    print('Removing the draft flag from the "{}" release\n'.format(tag_name))
    release.update_release(
        name=release.name, message=release.message, draft=False, prerelease=True)


def cleanup_draft_releases(github_token, github_api_url, travis_api_url, travis_repo_slug, travis_branch, travis_build_number):
    releases = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(
        travis_repo_slug).get_releases()
    print('Deleting unnecessary draft releases\n')
    prefix = 'continuous-{}-'.format(travis_branch)
    branch_unfinished_build_numbers = Travis.github_auth(
        github_token, travis_api_url).branch_unfinished_build_numbers(travis_repo_slug, travis_branch)
    releases_stored_previous = [r for r in releases if r.draft and r.tag_name.startswith(prefix) and re.match('^\d+-\d+$', r.tag_name[len(prefix):]) and int(
        r.tag_name[len(prefix):].split('-')[0]) < int(travis_build_number) and int(r.tag_name[len(prefix):].split('-')[0]) not in branch_unfinished_build_numbers]
    releases_stored_previous = sorted(releases_stored_previous, key=lambda r: r.tag_name[len(prefix):].split('-')[1])
    releases_stored_previous = sorted(releases_stored_previous, key=lambda r: r.tag_name[len(prefix):].split('-')[0])
    for release in releases_stored_previous:
        print('Deleting draft release with tag name "{}"...'.format(
            release.tag_name))
        release.delete_release()
        print(' Done\n')
    for release in stored_releases(releases, travis_branch, travis_build_number):
        print('Deleting draft release with tag name "{}"...'.format(
            release.tag_name))
        release.delete_release()
        print(' Done\n')
    print('All unnecessary draft releases are deleted\n')


def publish_releases(artifact_dir, latest_release, latest_release_name, latest_release_body, numbered_release, numbered_release_count, numbered_release_name, numbered_release_body, github_token, github_api_url, travis_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_build_id):
    if len(os.listdir(artifact_dir)) <= 0:
        raise ContinuousReleaseError('No artifacts were downloaded')
    releases = github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(
        travis_repo_slug).get_releases()
    if numbered_release:
        publish_numbered_release(numbered_release_count, releases, artifact_dir, numbered_release_name, numbered_release_body, github_token,
                                 github_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_build_id)
    if latest_release:
        publish_latest_release(releases, artifact_dir, latest_release_name, latest_release_body, github_token, github_api_url,
                               travis_api_url, travis_url, travis_repo_slug, travis_branch, travis_commit, travis_build_number, travis_build_id)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Continuous release publisher for GitHub+Travis-CI.')

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
    parser_latest_release = parser_publish.add_mutually_exclusive_group()
    parser_latest_release.add_argument('--latest-release', dest='latest_release', action='store_true',
                                    help='Publish latest release. The same "continuous-$BRANCH-latest" tag release will be re-used (re-created) by each build.')
    parser_latest_release.add_argument('--no-latest-release', dest='latest_release',
                                    action='store_false', help='Don\'t publish latest release.')
    parser_latest_release.set_defaults(latest_release=True)
    parser_publish.add_argument('--latest-release-name',
                                type=str, help='Release name text.')
    parser_publish.add_argument('--latest-release-body',
                                type=str, help='Release body text.')

    # publsh subparser -- numbered release
    parser_numbered_release = parser_publish.add_mutually_exclusive_group()
    parser_numbered_release.add_argument('--numbered-release', dest='numbered_release', action='store_true',
                                        help='Publish numbered release. A separate "continuous-$BRANCH-$BUILD" tag release will be made for each build.')
    parser_numbered_release.add_argument('--no-numbered-release', dest='numbered_release',
                                        action='store_false', help='Don\'t publish numbered release.')
    parser_numbered_release.set_defaults(numbered_release=True)
    parser_publish.add_argument('--numbered-release-count', type=int,
                                default=5, help='Number of numbered releases to keep.')
    parser_publish.add_argument(
        '--numbered-release-name', type=str, help='Release name text.')
    parser_publish.add_argument(
        '--numbered-release-body', type=str, help='Release body text.')

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

    def require_env(name):
        if name not in os.environ:
            raise ContinuousReleaseError('Required environment variable "{}" is not set'.format(name))
        return os.environ[name]

    try:
        if args.command == 'store':
            if not os.path.isdir(args.artifact_dir):
                raise ContinuousReleaseError('Directory "{}" doesn\'t exist'.format(args.artifact_dir))
            store_artifacts(args.artifact_dir, args.release_name, args.release_body, require_env('GITHUB_ACCESS_TOKEN'),
                            args.github_api_url, travis_url, require_env('TRAVIS_REPO_SLUG'), require_env('TRAVIS_BRANCH'),
                            require_env('TRAVIS_COMMIT'), require_env('TRAVIS_BUILD_NUMBER'),
                            require_env('TRAVIS_JOB_NUMBER').split('.')[1], require_env('TRAVIS_JOB_ID'))
        elif args.command == 'collect':
            if not os.path.isdir(args.artifact_dir):
                raise ContinuousReleaseError('Directory "{}" doesn\'t exist'.format(args.artifact_dir))
            collect_stored_artifacts(args.artifact_dir, require_env('GITHUB_ACCESS_TOKEN'), args.github_api_url,
                                     require_env('TRAVIS_REPO_SLUG'), require_env('TRAVIS_BRANCH'),
                                     require_env('TRAVIS_BUILD_NUMBER'))
        elif args.command == 'cleanup':
            cleanup_draft_releases(require_env('GITHUB_ACCESS_TOKEN'), args.github_api_url, travis_api_url,
                                   require_env('TRAVIS_REPO_SLUG'), require_env('TRAVIS_BRANCH'),
                                   require_env('TRAVIS_BUILD_NUMBER'))
        elif args.command == 'publish':
            if not os.path.isdir(args.artifact_dir):
                raise ContinuousReleaseError('Directory "{}" doesn\'t exist'.format(args.artifact_dir))
            if args.command == 'publish' and args.numbered_release_count <= 0:
                raise ContinuousReleaseError('--numbered-release-count must be greater than 0')
            publish_releases(args.artifact_dir, args.latest_release, args.latest_release_name, args.latest_release_body,
                             args.numbered_release, args.numbered_release_count, args.numbered_release_name,
                             args.numbered_release_body, require_env('GITHUB_ACCESS_TOKEN'), args.github_api_url,
                             travis_api_url, travis_url, require_env('TRAVIS_REPO_SLUG'), require_env('TRAVIS_BRANCH'),
                             require_env('TRAVIS_COMMIT'), require_env('TRAVIS_BUILD_NUMBER'), require_env('TRAVIS_BUILD_ID'))
        else:
            raise ContinuousReleaseError('Specify one of "store", "collect", "cleanup" or "publish" commands.')
    except ContinuousReleaseError as e:
        print('Error: {}'.format(str(e)))
        sys.exit(1)
