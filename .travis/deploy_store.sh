#!/usr/bin/env bash

set -exuo pipefail

ARTIFACTS_DIR="${PWD}/deploy"

cd .travis/tools/continuous_release
pip install -r requirements.txt
python ./continuous_release.py store "$ARTIFACTS_DIR"
