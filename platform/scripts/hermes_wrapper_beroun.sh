#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "/home/beroun/.hermes/hermes-agent/venv/bin/python" "/home/beroun/.hermes/hermes-agent/hermes" "$@"
