#!/usr/bin/env bash

set -exuo pipefail

cd .travis/tools/ci_release_publisher
pip install -r requirements.txt
mkdir ./deploy
python ./ci_release_publisher.py collect ./deploy
python ./ci_release_publisher.py cleanup
python ./ci_release_publisher.py publish --latest-release \
                                         --latest-release-prerelease \
                                         --numbered-release \
                                         --numbered-release-keep-count 3 \
                                         --numbered-release-keep-time "$((24 * 60 * 60))" \
                                         --numbered-release-prerelease \
                                         --tag-release \
                                         --tag-release-draft \
                                         --tag-release-prerelease \
                                         ./deploy
