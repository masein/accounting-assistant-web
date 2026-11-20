#!/bin/bash
# The app's tests (apple/ChatKit, Swift Testing) — with Xcode, or with the Command Line Tools alone.
#
# With the Command Line Tools (macOS 27), their build system now and then fails to load the Swift Testing macros
# ("plugin for module 'TestingMacros' not found"): a toolchain fault that comes and goes from one build to the next.
# Only that failure is retried (up to four tries); anything else — a test that fails — is reported at once.
#   apple/test.sh               every test
#   apple/test.sh --filter Chat a part of them
set -uo pipefail
cd "$(dirname "$0")/ChatKit"
if ! xcrun --find swift >/dev/null 2>&1 || ! xcodebuild -license check >/dev/null 2>&1; then
  if [ -d /Library/Developer/CommandLineTools ]; then export DEVELOPER_DIR=/Library/Developer/CommandLineTools; fi
fi
out=""
for try in 1 2 3 4; do
  out=$(swift test "$@" 2>&1)
  code=$?
  if [ $code -eq 0 ]; then
    echo "$out" | grep -E '✘|Test run' | tail -5
    exit 0
  fi
  if echo "$out" | grep -q "plugin for module 'TestingMacros' not found"; then
    echo "(the toolchain couldn't load the testing macros — trying again, $try of 4)"
    continue
  fi
  echo "$out" | tail -60
  exit $code
done
echo "$out" | tail -20
exit 1
