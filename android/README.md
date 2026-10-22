# Torrent Hub Android 1.1.0 — release build and connection setup

## Compatibility

- Ugoos AM6B Plus and Q4X: Android 9 (API 28), standard Android launcher and mouse.
- Phone: Android 15 (API 35), correct status/navigation bar insets.
- The APK remains a thin Java WebView shell; all tracker search happens in the HA add-on.
- Actual touchscreen/mouse/device validation must be performed after installation. CI verifies compilation, Java unit tests and signature; it cannot emulate the two Ugoos boxes.

## Connection profiles

Each saved profile has a **local URL** and an optional **remote URL**. At launch or when resuming, the app checks the local \`api/cache\` endpoint; if it is not reachable and a remote address is defined, it uses the remote one. The check tests the real Torrent Hub API, not Wi-Fi availability alone.

Examples:

- Local: \`http://192.168.1.100:8766\`
- Remote via HTTPS reverse proxy: \`https://torr.vapity.keenetic.link\`
- Remote via Tailscale: \`http://100.101.102.103:8766\`

Store multiple profiles through the **Сервер** button. Explicit HTTP/HTTPS URLs are preserved, and Tailscale DNS requires VPN access.

**Security:** never port-forward unauthenticated plain HTTP \`:8766\` to the internet. Use a VPN or a properly authenticated HTTPS reverse proxy.

## Signed Release build

**Never commit the signing key, its password or an unencrypted copy to GitHub.**
The automated release requires a *permanent* private key, reused for all subsequent releases. The previous RC1 APK uses a debug certificate, so Android cannot upgrade directly to the release-signed 1.1.0; back up your server URLs and remove RC1 before installing this first stable APK.

One-time private keystore creation (on a trusted computer):

\`\`\`sh
keytool -genkeypair -v -keystore torrent-hub-release.jks -alias torrenthub -keyalg RSA -keysize 3072 -validity 10000
\`\`\`

Create the following GitHub repository **Actions Secrets**:

- \`TORRENTHUB_RELEASE_KEYSTORE_B64\`: complete base64-encoded contents of the private .jks file
- \`TORRENTHUB_KEYSTORE_PASSWORD\`
- \`TORRENTHUB_KEY_ALIAS\` (e.g. \`torrenthub\`)
- \`TORRENTHUB_KEY_PASSWORD\`

PowerShell example for the base64 secret: \`[Convert]::ToBase64String([IO.File]::ReadAllBytes("torrent-hub-release.jks"))\`.

Save a secure offline backup of the keystore and credentials. Without them no final release APK can be safely published with a permanent signing identity.

Release workflow: \`.github/workflows/publish-android-1.1.0.yml\`.
Run it from the vetted revision or create tag \`android-v1.1.0\`. It will compile \`assembleRelease\`, cryptographically verify the signature, publish a downloadable APK and a SHA256 manifest. A CI test/debug build is never uploaded as a public release.

## Version

- \`applicationId: ru.barawkaa.torrenthub\`
- \`minSdk: 23\` (compatible with Android 9)
- \`targetSdk: 35\` (supports Android 15 insets)
- \`versionName: 1.1.0\` / \`versionCode: 2\`
