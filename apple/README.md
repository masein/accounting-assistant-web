# Accounting Chat — iPhone, iPad and Mac

The assistant's chat as a native app, and nothing else: sign in, the conversations, and the chat with its cards.
It is the same assistant as the web chat, the Android app and the Telegram/Bale bot. Nothing is recorded until
Confirm.

- **Sign-in:** username and password, with two-factor when it's on. It's the phone sign-in
  (`/api/mobile/v1/auth/…`), and the session shows under the user's devices as an iPhone, iPad or Mac.
- **Conversations:** a sidebar on the Mac and iPad, a list on the iPhone. "New conversation" starts one.
- **Answers:** drawn from the blocks contract (`docs/contracts/mobile-blocks.schema.json`):
  - text;
  - a **proposal** with Confirm / Cancel, then "Recorded" with **Undo** for its two minutes;
  - a **figure** (cash, what's owed) with its breakdown;
  - a **chart** (forecast, net worth);
  - the week's or the month's **review**.

  Every other kind (tables, files, approvals, a statement's intake) shows its `fallback_text`, which the contract
  guarantees reads well.
- **Language:** English, Persian, Arabic and Spanish, following the user's own setting on the server. Persian and
  Arabic are right to left, and a Persian message reads right to left in an English app too.
- **Network:** https only, plus `http://localhost` for a test server. The tokens live in the Keychain (this device
  only). An expired access token is renewed once and the request repeated. A refused renewal goes back to sign-in.
- **A message sent twice is answered once:** each message carries its own id, so "Try again" after a dropped
  connection never posts twice.

## Layout

| path | what |
|------|------|
| `ChatKit/` | A Swift package holding the whole app. `ChatCore` is the API client, the session, the chat's state and its words, with no UI, so `swift test` runs it on any Mac. `ChatKit` is the SwiftUI screens on top. |
| `AccountingChat/` | The app's entry point, icon, and the debug-only scripted run (`Automation.swift`). |
| `AccountingChat.xcodeproj` | One multiplatform target (iOS 17+, macOS 14+), bundle id `app.accountingassistant.chat`. |

## Build and test

```bash
cd apple/ChatKit && swift test                      # the client, the chat's state, the contract recordings
xcodebuild -project apple/AccountingChat.xcodeproj -scheme AccountingChat \
  -destination 'platform=iOS Simulator,name=iPhone 17' build
xcodebuild -project apple/AccountingChat.xcodeproj -scheme AccountingChat -destination 'platform=macOS' build
```

To run it on a device, open the project in Xcode, choose your team under Signing & Capabilities, and run. The Mac
build signs to run locally without a team.

`.github/workflows/apple.yml` runs the package tests and both builds when `apple/`, the contract or the workflow
changes.

## Screenshots and an end-to-end run

Against a local server (the dev stack on `localhost:18000`) with `tests_e2e/fake_llm.py` standing in for the AI:

- **iPhone / iPad:** run it in the Simulator and sign in to `http://localhost:18000`.
- **Mac:** a debug build runs a script from its launch environment. It signs in, sends, confirms the first card,
  and saves its own window after each step. No screen-recording permission is needed.
  ```bash
  AC_SERVER=http://localhost:18000 AC_USER=admin AC_PASS=… AC_SEND="hi|paid 20 for lunch" AC_CONFIRM=1 \
  AC_SNAPSHOT_DIR=/tmp/shots "<DerivedData>/Build/Products/Debug/AccountingChat.app/Contents/MacOS/AccountingChat"
  ```
