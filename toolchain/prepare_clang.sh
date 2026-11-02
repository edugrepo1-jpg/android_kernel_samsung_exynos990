#!/usr/bin/env bash
# Fetch the exact compiler selected by GMKernel through verified Git objects.
# Gitiles archive responses can end early with HTTP 200; curl --retry cannot
# detect that failure. Never stream that response directly into the build.
set -euo pipefail

CLANG_COMMIT=dc339a8e5a421a55f458674981fdce18a451f298
CLANG_REVISION=clang-r450784d
CLANG_REPOSITORY=https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86

validate_toolchain()
{
    local directory="$1"
    [ -f "$directory/.unica-clang-source" ] || return 1
    [ "$(cat "$directory/.unica-clang-source")" = "$CLANG_COMMIT/$CLANG_REVISION" ] || return 1
    [ -x "$directory/bin/clang-14" ] || return 1
    [ -f "$directory/lib64/libc++.so.1" ] || return 1
    [ -s "$directory/.unica-clang-sha256" ] || return 1
    (cd "$directory" && sha256sum --status -c .unica-clang-sha256) || return 1
    "$directory/bin/clang-14" --version >/dev/null || return 1
    "$directory/bin/ld.lld" --version >/dev/null || return 1
}

prepare_toolchain()
(
    [ "$#" -eq 1 ] && [ -n "$1" ] || { echo 'Expected one toolchain installation directory' >&2; return 1; }
    local directory parent stage attempt ready
    directory="$(realpath -m -- "$1")"
    [ "$directory" != / ] || return 1
    if validate_toolchain "$directory"; then
        echo "Reusing integrity-verified $CLANG_REVISION ($CLANG_COMMIT)"
        return 0
    fi
    parent="$(dirname "$directory")"
    mkdir -p "$parent"
    stage="$(mktemp -d "$parent/.clang-staging.XXXXXXXX")"
    # The only recursive cleanup is the directory returned by mktemp under
    # this resolved parent. An existing compiler is preserved until success.
    trap 'case "$stage" in "$parent"/.clang-staging.*) rm -rf -- "$stage" ;; esac' EXIT
    git init -q "$stage/source"
    git -C "$stage/source" remote add origin "$CLANG_REPOSITORY"
    git -C "$stage/source" config core.sparseCheckout true
    git -C "$stage/source" sparse-checkout set --cone "$CLANG_REVISION"
    ready=false
    for attempt in 1 2 3; do
        if git -C "$stage/source" -c http.lowSpeedLimit=1024 -c http.lowSpeedTime=60 \
                fetch --depth 1 --filter=blob:none origin "$CLANG_COMMIT" &&
                git -C "$stage/source" checkout --detach "$CLANG_COMMIT"; then
            ready=true
            break
        fi
        echo "Compiler Git transfer attempt $attempt failed; retrying verified objects" >&2
    done
    "$ready" || return 1
    [ "$(git -C "$stage/source" rev-parse HEAD)" = "$CLANG_COMMIT" ] || return 1
    [ -z "$(git -C "$stage/source" status --porcelain --untracked-files=no)" ] || return 1
    [ -x "$stage/source/$CLANG_REVISION/bin/clang-14" ] || return 1
    [ -f "$stage/source/$CLANG_REVISION/lib64/libc++.so.1" ] || return 1
    mv "$stage/source/$CLANG_REVISION" "$stage/install"
    printf '%s/%s\n' "$CLANG_COMMIT" "$CLANG_REVISION" > "$stage/install/.unica-clang-source"
    (cd "$stage/install" && find . -type f ! -name '.unica-clang-sha256' -print0 |
        LC_ALL=C sort -z | xargs -0 sha256sum > .unica-clang-sha256)
    validate_toolchain "$stage/install" || return 1
    if [ -e "$directory" ] || [ -L "$directory" ]; then
        mv -- "$directory" "$stage/previous"
    fi
    if ! mv -- "$stage/install" "$directory"; then
        if [ -e "$stage/previous" ] || [ -L "$stage/previous" ]; then
            mv -- "$stage/previous" "$directory"
        fi
        return 1
    fi
    echo "Installed integrity-verified $CLANG_REVISION ($CLANG_COMMIT)"
)

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    prepare_toolchain "$@"
fi
