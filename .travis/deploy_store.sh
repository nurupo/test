#!/usr/bin/env bash

set -exuo pipefail

if [ "$TRAVIS_TEST_RESULT" != "0" ]; then
  echo "Build has failed, skipping publishing"
  exit 0
fi

if [ "$TRAVIS_TEST_RESULT" != "false" ]; then
  echo "Skipping publishing in a Pull Request"
  exit 0
fi

ARTIFACTS_DIR="${PWD}/deploy"

cd .travis/tools/ci_release_publisher
pip install -r requirements.txt
python ./ci_release_publisher.py store "$ARTIFACTS_DIR"
