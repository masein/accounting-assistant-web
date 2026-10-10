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

```bash
./gradlew :app:assembleDebug
```

## Test

The unit tests cover:
- the client against a mock server: sign-in, refresh-and-retry, the end of a session, 426;
- block parsing;
- number formatting.

```bash
./gradlew :app:testDebugUnitTest
```

Screenshot tests render the screens in Persian and English, in light and dark,
into `app/build/outputs/roborazzi`:

```bash
./gradlew :app:recordRoborazziDebug
```

## Run against a local server

A debug build calls `http://10.0.2.2:8000`, which is the developer's machine as seen
from the emulator. Plain HTTP is allowed only there, and only in debug builds
(`src/debug/res/xml/network_security_config.xml`). To use another port:

```bash
./gradlew :app:installDebug -PapiBase=http://10.0.2.2:8010
```

## Before the first store upload

- The `applicationId` (`app.accountingassistant.android`) is permanent once
  published: settle it first.
- The release `API_BASE` is a placeholder.
- Signing, and the `play`, `bazaar` and `direct` flavours (roadmap §7.3), come with the first release.
