# Accountant for Android

The chat-only Android app (roadmap: `docs/ROADMAP_ANDROID_CHAT.md`; design:
`docs/design/android-chat.html`). One screen: the books badge, the
conversation, the composer. It talks to the server's `/api/mobile/v1`.

**Stack:**
- Kotlin with Jetpack Compose, on Material 3 Expressive (`material3` 1.5);
- OkHttp and kotlinx.serialization for the network;
- DataStore for storage, with the session sealed by an Android Keystore key;
- Robolectric with Roborazzi for screenshot tests that need no emulator.

**SDK levels:** minSdk 26, targetSdk 36.

## Build

Java 21 and the Android SDK (platform 36, build tools 36) are needed. Point the
build at the SDK in `local.properties` (`sdk.dir=…`) or with `ANDROID_HOME`.

Two builds, by where the app is installed from (roadmap §7.1, §11.5):
- **`play`:** Google Play. It doesn't read SMS until Google grants the exception; an SMS is shared into the app instead.
- **`direct`:** the APK handed out directly (and Bazaar, once its rules are checked). It reads bank SMS as they arrive, from senders the user names (`app/src/direct/AndroidManifest.xml`, `sms/`).

```bash
./gradlew :app:assembleDirectDebug     # or :app:assemblePlayDebug
```

## Test

The unit tests cover:
- the client against a mock server: sign-in, refresh-and-retry, the end of a session, 426;
- block parsing;
- number formatting;
- bank SMS: what is kept, asked about and sent;
- each build's manifest (`src/testDirect`, `src/testPlay`).

```bash
./gradlew :app:testDirectDebugUnitTest
./gradlew :app:testPlayDebugUnitTest --tests '*StoreBuildTest*'
```

Screenshot tests render the screens in Persian and English, in light and dark,
into `app/build/outputs/roborazzi`:

```bash
./gradlew :app:recordRoborazziDirectDebug
```

## Run against a local server

A debug build calls `http://10.0.2.2:8000`, which is the developer's machine as seen
from the emulator. Plain HTTP is allowed only there, and only in debug builds
(`src/debug/res/xml/network_security_config.xml`). To use another port:

```bash
./gradlew :app:installDirectDebug -PapiBase=http://10.0.2.2:8010
```

An SMS to the emulator, to see the bank SMS reading (the switch on in the account sheet):

```bash
adb emu sms send 98700717 "بانک ملت برداشت:125,000 مانده:1,234,567"
```

## Before the first store upload

- The `applicationId` (`app.accountingassistant.android`) is permanent once
  published: settle it first.
- The release `API_BASE` is a placeholder.
- Signing, and a `bazaar` build once Bazaar's rules on reading SMS are checked (roadmap §7.3), come with the first release.
- Play's Permissions Declaration Form for reading SMS (the money-management exception) is the owner's to file; until it is granted, `play` doesn't ask.
