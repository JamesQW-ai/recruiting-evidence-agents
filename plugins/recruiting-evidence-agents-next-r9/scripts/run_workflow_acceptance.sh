#!/usr/bin/env bash
set -euo pipefail

# Offline only: no Feishu API calls, no external messages, no Base writes.
cd "$(dirname "$0")/.."
python3 -m unittest tests/test_workflow_acceptance.py
python3 -m unittest discover -s tests -p 'test_*.py'
python3 ../../tests/test_frozen_evidence.py
