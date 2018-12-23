#!/usr/bin/env bash

set -exuo pipefail

if [ ! -z "$TRAVIS_PULL_REQUEST" ] && [ "$TRAVIS_PULL_REQUEST" != "false" ]; then
  echo "Skipping publishing in a Pull Request"
  exit 0
fi

cd .travis/tools/ci_release_publisher
pip install -r requirements.txt
python ./ci_release_publisher.py cleanup
