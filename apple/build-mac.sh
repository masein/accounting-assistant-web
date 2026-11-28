#!/bin/bash
# Builds the Mac app from the ChatKit package into apple/build/AccountingChat.app — with Xcode, or with the Command
# Line Tools alone (no Xcode, no Xcode licence). Signed to run on this Mac (ad hoc); to hand it to someone else,
# build it in Xcode with a Developer ID instead.
#
#   apple/build-mac.sh            release build
#   apple/build-mac.sh debug      debug build: includes the scripted run for screenshots (apple/README.md)
#   open apple/build/AccountingChat.app
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
CONFIG=${1:-release}
VERSION=1.0.0

# Xcode installed but its licence not accepted: build with the Command Line Tools instead
if ! xcrun --find swift >/dev/null 2>&1 || ! xcodebuild -license check >/dev/null 2>&1; then
  if [ -d /Library/Developer/CommandLineTools ]; then export DEVELOPER_DIR=/Library/Developer/CommandLineTools; fi
fi

# its own build folder: with the Command Line Tools, building this product in the folder `swift test` uses leaves the
# Swift Testing macros unloadable there for the next test run (a toolchain fault), so the two never share one
SCRATCH="$HERE/build/swiftpm"
cd "$HERE/ChatKit"
swift build -c "$CONFIG" --product AccountingChat --scratch-path "$SCRATCH"
BIN="$(swift build -c "$CONFIG" --product AccountingChat --scratch-path "$SCRATCH" --show-bin-path)/AccountingChat"

APP="$HERE/build/AccountingChat.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/AccountingChat"

# the icon, from the same images the Xcode project's asset catalog uses
ICONS="$HERE/AccountingChat/Assets.xcassets/AppIcon.appiconset"
SET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$SET"
for pair in 16:icon_16x16 32:icon_16x16@2x 32:icon_32x32 64:icon_32x32@2x 128:icon_128x128 256:icon_128x128@2x \
            256:icon_256x256 512:icon_256x256@2x 512:icon_512x512 1024:icon_512x512@2x; do
  cp "$ICONS/mac-${pair%%:*}.png" "$SET/${pair#*:}.png"
done
iconutil -c icns "$SET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key><string>AccountingChat</string>
	<key>CFBundleIdentifier</key><string>app.accountingassistant.chat</string>
	<key>CFBundleName</key><string>Accounting Chat</string>
	<key>CFBundleDisplayName</key><string>Accounting Chat</string>
	<key>CFBundleIconFile</key><string>AppIcon</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleShortVersionString</key><string>$VERSION</string>
	<key>CFBundleVersion</key><string>1</string>
	<key>LSMinimumSystemVersion</key><string>14.0</string>
	<key>LSApplicationCategoryType</key><string>public.app-category.finance</string>
	<key>NSHighResolutionCapable</key><true/>
	<key>NSPrincipalClass</key><string>NSApplication</string>
	<key>NSMicrophoneUsageDescription</key><string>Hold the microphone to say what you spent or earned; your words are written into the message for you to check.</string>
	<key>NSPhotoLibraryUsageDescription</key><string>Attach a photo of a receipt or an invoice to a message.</string>
	<!-- https only, except this machine: a test server on localhost (AppModel.serverURL refuses plain http elsewhere) -->
	<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

codesign --force --sign - --timestamp=none "$APP" 2>/dev/null
echo "Built $APP ($CONFIG, $(du -sh "$APP" | cut -f1))"
