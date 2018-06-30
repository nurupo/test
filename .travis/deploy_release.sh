#!/usr/bin/env bash

set -exuo pipefail

cd .travis/tools/continuous_release
pip install -r requirements.txt
mkdir /tmp/deploy
python ./continuous_release.py collect /tmp/deploy
python ./continuous_release.py cleanup
python ./continuous_release.py publish --latest-release --numbered-release --numbered-release-count 3 /tmp/deploy
