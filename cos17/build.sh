#!/usr/bin/env bash
# Build the public r8s kernel only. No COS firmware upload or device access.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
mkdir -p out/cos17-artifacts
exec > >(tee out/cos17-kernel-build.log) 2>&1

# A shallow CI checkout may not contain the immutable charging baseline.
# Fetch that exact object explicitly; growing commit history must not break CI.
BASE_COMMIT=16df24ea7b48eb426fce2ff6b751585cc81a19a4
if ! git cat-file -e "$BASE_COMMIT^{commit}" 2>/dev/null; then
    git fetch --no-tags --depth=1 origin "$BASE_COMMIT"
fi
test "$(git rev-parse "$BASE_COMMIT^{commit}")" = "$BASE_COMMIT"

test "$(git -C KernelSU-Next rev-parse HEAD)" = c81cdf51c3bb606836c6fc9a7cc858053eb0bc62
git -C KernelSU-Next apply --check "$PWD/cos17/ksu-sepolicy.patch"
git -C KernelSU-Next apply "$PWD/cos17/ksu-sepolicy.patch"
python3 cos17/verify.py source
bash build.sh -m r8s -k y -r n
python3 cos17/verify.py output

cp out/arch/arm64/boot/Image out/cos17-artifacts/Image
cp out/System.map out/cos17-artifacts/System.map
cp out/.config out/cos17-artifacts/kernel.config
cp build/out/r8s/dtb.img out/cos17-artifacts/dtb.img
cp build/out/r8s/dtbo.img out/cos17-artifacts/dtbo.img
git diff --binary 16df24ea7b48eb426fce2ff6b751585cc81a19a4 HEAD > out/cos17-artifacts/kernel-source.patch
git -C KernelSU-Next diff --binary > out/cos17-artifacts/ksu-source.patch
python3 cos17/verify.py manifest
