#!/usr/bin/env bash
# Source-controlled replacement for the existing Hermes tick_carry.sh.
# Successful, expired and failed runs remain visible; preserve the Python exit.
exec /usr/bin/python3 -B /opt/sniper/current/platform/research/p014_capture.py \
  --output /home/wwwenda/p014/paper/ticks.jsonl \
  --end 2026-10-06T19:00:00+00:00
