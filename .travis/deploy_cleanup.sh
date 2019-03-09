#!/usr/bin/env bash

set -exuo pipefail

if [ ! -z "$TRAVIS_PULL_REQUEST" ] && [ "$TRAVIS_PULL_REQUEST" != "false" ]; then
  echo "Skipping publishing in a Pull Request"
  exit 0
fi

cd .travis/tools
pip install -r ci_release_publisher/requirements.txt
python -m ci_release_publisher cleanup
