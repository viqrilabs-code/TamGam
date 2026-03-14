# Android Release Guide

This guide prepares and ships the Android app (`com.tamgam.app`) to Play Console.

## 1) Prerequisites

- Android Studio installed (with SDK + build tools).
- Java 17 available (`JAVA_HOME` set).
- Firebase Android app configured for package `com.tamgam.app`.
- Google Play Console app created.

## 2) Configure Release Signing

Generate a keystore once:

```powershell
keytool -genkeypair `
  -v `
  -keystore C:\secure\tamgam-release.jks `
  -alias tamgam `
  -keyalg RSA `
  -keysize 4096 `
  -validity 10000
```

Set signing properties in `~/.gradle/gradle.properties` (recommended):

```properties
TAMGAM_RELEASE_STORE_FILE=C:/secure/tamgam-release.jks
TAMGAM_RELEASE_STORE_PASSWORD=your_store_password
TAMGAM_RELEASE_KEY_ALIAS=tamgam
TAMGAM_RELEASE_KEY_PASSWORD=your_key_password
```

Do not commit keystore or passwords.

## 3) Set Release Version

In `tamgam-android-app/gradle.properties`, update:

- `TAMGAM_ANDROID_VERSION_CODE` (must increase every upload)
- `TAMGAM_ANDROID_VERSION_NAME` (user-visible)

Default release URL is:

- `TAMGAM_WEB_BASE_URL_RELEASE=https://tamgam.in`

## 4) Build AAB (Play Store)

From `tamgam-android-app`:

```powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
.\gradlew.bat clean :app:bundleRelease
```

Output:

- `app/build/outputs/bundle/release/app-release.aab`

## 5) Build APK (Internal QA)

```powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
.\gradlew.bat :app:assembleRelease
```

Output:

- `app/build/outputs/apk/release/app-release.apk`

## 6) Play Console Deployment

1. Go to Play Console -> your app -> Testing -> Internal testing.
2. Create a new release and upload `app-release.aab`.
3. Add release notes.
4. Roll out to internal testers first.
5. Validate login, subscription, dashboard, and payment flows.
6. Promote to closed/open/production after QA sign-off.

## 7) Release QA Checklist (Must Pass)

- App launches and loads `https://tamgam.in`.
- Phone OTP login succeeds on real device.
- Teacher subscription purchase succeeds.
- Post-payment access is unlocked in app.
- Dashboard loads without API errors.
- WebView navigation/back behavior is correct.
- No cleartext production traffic (release build uses HTTPS).
