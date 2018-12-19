#!/usr/bin/env bash

set -exuo pipefail

if [ -z "$TRAVIS_TEST_RESULT" ] && [ "$TRAVIS_TEST_RESULT" != "0" ]; then
  echo "Build has failed, skipping publishing"
  exit 0
fi

if [ "$TRAVIS_PULL_REQUEST" != "false" ]; then
  echo "Skipping publishing in a Pull Request"
  exit 0
fi

cd .travis/tools/ci_release_publisher
pip install -r requirements.txt
mkdir ./deploy
python ./ci_release_publisher.py collect ./deploy
python ./ci_release_publisher.py cleanup
python ./ci_release_publisher.py publish --latest-release \
                                         --latest-release-prerelease \
                                         --numbered-release \
                                         --numbered-release-keep-count 3 \
                                         --numbered-release-prerelease \
                                         --tag-release \
                                         ./deploy
