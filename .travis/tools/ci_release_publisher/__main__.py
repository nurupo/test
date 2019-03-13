# -*- coding: utf-8 -*-

import argparse
import github
import logging
import os
import sys

from . import config
from . import env
from . import exception
from . import latest_release, numbered_release, tag_release
from . import temporary_draft_release
from . import travis
from .__version__ import __description__

logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO, datefmt='%H:%M:%S')

release_kinds = [latest_release, numbered_release, tag_release]

parser = argparse.ArgumentParser(description=__description__)

# Travis
parser_travis = parser.add_mutually_exclusive_group()
parser_travis.add_argument('--travis-public', dest='travis_type', action='store_const', const='public',
                            help='Use API of the free Travis-CI service, i.e. "https://travis-ci.org".')
parser_travis.add_argument('--travis-private', dest='travis_type', action='store_const', const='private',
                            help='Use API of the paid Travis-CI service for GitHub private repositories, i.e. "https://travis-ci.com".')
parser_travis.add_argument('--travis-enterprise', dest='travis_type', metavar='TRAVIS_URL', type=str,
                            help='Use API of Travis-CI running under a personal domain. Specify the actual URL, e.g. "http://travis.example.com".')
parser.set_defaults(travis_type='public')

parser.add_argument('--github-api-url', type=str, default="",
                    help='Use custom GitHib API URL, e.g. for self-hosted GitHub Enterprise instance. This should be an URL to the API endpoint, e.g. "https://api.github.com".')

subparsers = parser.add_subparsers(dest='command')

# store subparser
parser_store = subparsers.add_parser('store', help='Store job artifacts in a draft release for the later collection.')
parser_store.add_argument('artifact_dir', metavar='artifact-dir', help='Path to a directory containing artifacts that need to be stored.')
temporary_draft_release.args(parser_store)

# cleanup store subparser
parser_cleanup_store = subparsers.add_parser('cleanup_store', help='Delete the draft release created by the "store" for this particular job.')

# collect subparser
parser_collect = subparsers.add_parser('collect', help='Collect the previously stored build artifacts in a directory.')
parser_collect.add_argument('artifact_dir', metavar='artifact-dir', help='Path to a directory where artifacts should be collected to.')

# cleanup subparser
parser_collect = subparsers.add_parser('cleanup',
                                        help='Delete draft releases created by the "store" command of this build only, '
                                             'as well as leftover draft releases created by previous builds\' "publish" command for this branch only.')

# publish subparser
parser_publish = subparsers.add_parser('publish', help='Publish a release with all artifacts from a directory.')
parser_publish.add_argument('artifact_dir', metavar='artifact-dir', help='Path to a directory containing build artifacts to publish.')

for r in release_kinds:
    r.publish_args(parser_publish)

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

try:
    if args.command == 'store':
        if not os.path.isdir(args.artifact_dir):
            raise exception.CIReleasePublisherError('Directory "{}" doesn\'t exist.'.format(args.artifact_dir))
        if len(os.listdir(args.artifact_dir)) <= 0:
            raise exception.CIReleasePublisherError('No artifacts were found in "{}" directory.'.format(args.artifact_dir))
        temporary_draft_release.publish_validate_args(args)
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        temporary_draft_release.publish_with_args(args, releases, args.artifact_dir, args.github_api_url, travis_api_url, travis_url)
    elif args.command == 'cleanup_store':
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        temporary_draft_release.cleanup_store(releases, args.github_api_url)
    elif args.command == 'collect':
        if not os.path.isdir(args.artifact_dir):
            raise exception.CIReleasePublisherError('Directory "{}" doesn\'t exist.'.format(args.artifact_dir))
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        temporary_draft_release.download(releases, args.artifact_dir)
    elif args.command == 'cleanup':
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        if env.optional('TRAVIS_TAG'):
            branch_unfinished_build_numbers = travis.Travis.github_auth(env.required('GITHUB_ACCESS_TOKEN'), travis_api_url).branch_unfinished_build_numbers(env.required('TRAVIS_REPO_SLUG'), env.required('TRAVIS_TAG'))
        else:
            branch_unfinished_build_numbers = travis.Travis.github_auth(env.required('GITHUB_ACCESS_TOKEN'), travis_api_url).branch_unfinished_build_numbers(env.required('TRAVIS_REPO_SLUG'), env.required('TRAVIS_BRANCH'))
        temporary_draft_release.cleanup(releases, branch_unfinished_build_numbers, args.github_api_url)
        for r in release_kinds:
            r.cleanup(releases, branch_unfinished_build_numbers, args.github_api_url)
    elif args.command == 'publish':
        if not os.path.isdir(args.artifact_dir):
            raise exception.CIReleasePublisherError('Directory "{}" doesn\'t exist.'.format(args.artifact_dir))
        if len(os.listdir(args.artifact_dir)) <= 0:
            raise exception.CIReleasePublisherError('No artifacts were found in "{}" directory.'.format(args.artifact_dir))
        if not any(r.publish_validate_args(args) for r in release_kinds):
            raise exception.CIReleasePublisherError('You must specify what kind of release you would like to publish.')
        releases = github.Github(login_or_token=env.required('GITHUB_ACCESS_TOKEN'), base_url=args.github_api_url).get_repo(env.required('TRAVIS_REPO_SLUG')).get_releases()
        for r in release_kinds:
            r.publish_with_args(args, releases, args.artifact_dir, args.github_api_url, travis_api_url, travis_url)
    else:
        raise exception.CIReleasePublisherError('Specify one of "store", "cleanup_store", "collect", "cleanup" or "publish" commands.')
except exception.CIReleasePublisherError as e:
    logging.error('Error: {}'.format(str(e)))
    sys.exit(1)
