#!/usr/bin/env bash

set -exuo pipefail

if [ ! -z "$TRAVIS_PULL_REQUEST" ] && [ "$TRAVIS_PULL_REQUEST" != "false" ]; then
  echo "Skipping publishing in a Pull Request"
  exit 0
fi

if [ -z "$TRAVIS_TEST_RESULT" ] && [ "$TRAVIS_TEST_RESULT" == "0" ]; then
  echo "Build has not failed, skipping cleanup"
  exit 0
fi

cd .travis/tools
pip install -r ci_release_publisher/requirements.txt
python -m ci_release_publisher cleanup_publish
python -m ci_release_publisher cleanup_store --scope current-build previous-finished-builds \
                                             --release complete incomplete
