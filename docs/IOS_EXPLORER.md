# Arborary.World for iOS

The website at arborary.world/explore and the iPhone app use the same Vue explorer in `src/`. The iOS target embeds the Vite entry page, map workers and DuckDB WASM in the app. Map tiles, tree blocks and enrichment still require a network connection. Near Me uses foreground iOS location; Explore City works without granting location. The first build is for map testing: Firebase accounts/contributions and optional keyed services are not configured in the mobile workflow.

## Develop on Windows

Use Node 22 and pnpm 10. From `src/`:

```powershell
pnpm install --frozen-lockfile
pnpm dev                 # edit the shared explorer in a browser
pnpm ios:sync            # type-check, build dist-ios, sync to the Xcode project
pnpm exec vite preview --mode ios   # smoke-test the bundled client in a browser
```

The Xcode project lives at `src/ios/App/App.xcodeproj`. Do not run `cap init` or `cap add ios` again. The native sources belong in git; the copied web bundle does not. The distribution build uses the root of the local app, regardless of the website's APP_BASE. Make changes in the shared client, then sync again. Supply no production keys to run the map.

## First internal TestFlight build

1. Enroll in the [Apple Developer Program](https://developer.apple.com/programs/enroll/) if needed. Register an explicit App ID for **world.arborary.explorer**, then [create the iOS app record](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-a-new-app/) in App Store Connect using that ID and the display name **Arborary.World**.
2. Connect the **greenmtnboy/tree_reporting** repository in Codemagic. In the team's integrations, add an [App Store Connect team API key](https://docs.codemagic.io/yaml-publishing/app-store-connect/) named **Arborary**. Keep the private key there. In team code signing, [create or upload an Apple Distribution certificate with its private key and fetch an App Store distribution profile](https://docs.codemagic.io/yaml-code-signing/signing-ios/) matching the bundle ID and certificate.
3. Select the branch containing `codemagic.yaml` and manually start **Arborary.World · internal TestFlight**. It builds and signs the `App` scheme and uploads an IPA with a new build number. No public release or external beta review is requested.
4. After Apple processes the build, complete any export compliance questions, add the build and your Apple ID to an internal TestFlight group, then install it with TestFlight on your iPhone.

The GitHub **Explorer iOS simulator** workflow builds and launches an unsigned app on macOS without Apple or Codemagic credentials. Its simulator zip cannot be installed on an iPhone.

## First device pass

- Cold-launch without a connection: the entry screen should load. Restore the connection before requesting trees.
- Deny location, then choose Explore City. In a mapped neighborhood, enable While Using the App and try Near Me; confirm the pin and a nearby tree's details.
- Pan and zoom through London and New York; open and dismiss several tree sheets. Check that city selection and the bottom controls remain reachable. Background and resume the app.
- Record phone model, iOS version, city, time to first tree and any reload or blank map. Use Instruments on a physical device before drawing memory or performance conclusions from the simulator.

