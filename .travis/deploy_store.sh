#!/usr/bin/env bash

set -exuo pipefail

ARTIFACTS_DIR="${PWD}/deploy"

cd .travis/tools/ci_release_publisher
pip install -r requirements.txt
python ./ci_release_publisher.py store "$ARTIFACTS_DIR"
