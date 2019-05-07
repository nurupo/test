# -*- coding: utf-8 -*-

import cgi
import github
import logging
import os
import requests
import shutil

from . import config

# Various GitHub helpers

def download_artifact(github_token, src_url, dst_dir):
    # API doc: https://developer.github.com/v3/repos/releases/#get-a-single-release-asset
    # In order to download draft artifacts you need a GitHub token with write access to that repo,
    # otherwise you can't download those artifacts, the download URLs are "private" in a sense.
    headers = {
        'Authorization': 'token {}'.format(github_token),
        'Accept': 'application/octet-stream',
        'User-Agent': config.user_agent,
    }
    r = requests.get(src_url, headers=headers, allow_redirects=True, stream=True)
    filename = ''
    # Figure out filename
    # 1. Proper way of doing it
    cd = r.headers.get('Content-Disposition')
    if cd:
        _, params = cgi.parse_header(cd)
        if 'filename' in params:
            filename = params['filename']
    # 2. Last resort way of doing it
    if not filename:
        filename = src_url.split('/')[-1]
    filepath = os.path.join(dst_dir, filename)
    with open(filepath, 'wb') as f:
        shutil.copyfileobj(r.raw, f)
    return filepath

def download_artifcats(github_token, release, dst_dir):
    logging.info('Downloading artifacts from "{}" release.'.format(release.tag_name))
    # This might look dumb but get_assets() returns a custom type that is a lazy list which doesn't support len(),
    # so we eagerly load everything as we want to get len() and we'd load all of the assets later anyway.
    artifacts = [asset for asset in release.get_assets()]
    logging.info('Found {} artifact(s) in the release.'.format(len(artifacts)))
    for artifact in artifacts:
        logging.info('\tDownloading artifact "{}" ({} bytes).'.format(artifact.name, artifact.size))
        download_artifact(github_token, artifact.url, dst_dir)
    logging.info('All artifacts from "{}" release are downloaded.'.format(release.tag_name))

def upload_artifacts(src_dir, release):
    logging.info('Uploading artifacts to "{}" release.'.format(release.tag_name))
    artifacts = sorted(os.listdir(src_dir))
    logging.info('Found {} artifact(s) in "{}" directory.'.format(len(artifacts), src_dir))
    for artifact in artifacts:
        artifact_path = os.path.join(src_dir, artifact)
        if os.path.isfile(artifact_path):
            logging.info('\tStoring "{}" ({} bytes) artifact in the release.'.format(artifact, os.path.getsize(artifact_path)))
            release.upload_asset(artifact_path)
    logging.info('All artifacts for "{}" release are uploaded.'.format(release.tag_name))

def delete_release_with_tag(release, github_token, github_api_url, travis_repo_slug):
    logging.info('Deleting a release with the tag name "{}".'.format(release.tag_name))
    release.delete_release()
    # Published releases create tags and we don't want to keep the tags
    if not release.draft:
        logging.info('Deleting "{}" tag.'.format(release.tag_name))
        github.Github(login_or_token=github_token, base_url=github_api_url).get_repo(travis_repo_slug).get_git_ref('tags/{}'.format(release.tag_name)).delete()
