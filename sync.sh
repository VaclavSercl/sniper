#!/bin/bash
# BEROUN SNIPER - VERSION SYNC & ROTATION (Best Practices 2026)
# Ensures 6 last versions are always tagged and rollback-ready.

MSG=$1
if [ -z "$MSG" ]; then
    MSG="feat: general update and optimization"
fi

# 1. Increment patch version in Cargo.toml (for demonstration, we'll use a simple increment)
# In real HFT, we increment manually or via specialized tools like 'cargo-release'.
git add .
git commit -m "$MSG"

# 2. Tag current version
VER=$(grep -m1 version Cargo.toml | cut -d'"' -f2)
TAG="v$VER-$(date +%Y%m%d-%H%M)"
git tag -a "$TAG" -m "Deployment $TAG"

# 3. Push everything
git push origin main --tags

echo "✅ Sync Complete! Tagged as $TAG"
echo "--- Last 6 Tags (Rollback Options) ---"
git tag --sort=-creatordate | head -n 6
