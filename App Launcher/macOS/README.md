# macOS App Launcher

Launches macOS GUI applications in the foreground using LaunchServices.

## Features

1. **LaunchServices launch** via `open` command for reliable foreground behavior
2. **Bundle ID auto-discovery** from Info.plist
3. **Foreground activation** via AppleScript
4. **Graceful quit** via AppleScript with `pkill` fallback
5. **Status polling** every 5 seconds
6. **Auto-restart with exponential backoff** on crash
7. **Interruption detection** and status warnings

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| App Path | Yes | Path to `.app` bundle or binary inside `Contents/MacOS/` |
| App Arguments | No | Arguments passed via `--args` |
| Bundle ID | No | Auto-discovered from Info.plist if not set |
| Full Screen | No | Enter native macOS fullscreen mode (covers dock and menu bar) |
| Power State on Start | No | On / Off / (previous) |

## Finding Bundle ID

```bash
osascript -e 'id of app "Safari"'
# or
mdls -name kMDItemCFBundleIdentifier /Applications/Safari.app
```

## Auto-Restart Behavior

When the app crashes or is closed externally while Desired Power is "On":

1. **First failure**: restart immediately
2. **Second failure**: wait 10s before restart
3. **Third failure**: wait 20s before restart
4. **...continues doubling** up to 5 minutes max
5. **After 10 consecutive failures**: stop trying, emit error status
6. **After 60s stable**: reset restart counter
7. **Manual Power Off/On**: reset all restart state

## Permissions

On first use, macOS will prompt for permissions:

1. **"java" wants to control "System Events"** - Required for activation and fullscreen. Click "OK" to allow.

2. **Accessibility permissions** (if fullscreen fails) - Go to System Preferences > Security & Privacy > Privacy > Accessibility and add the Java/Nodel process.

These prompts appear once per machine. If denied, activation and fullscreen features won't work but the app will still launch.

## Notes

- Apps launched by nodehost when installed as a service will not be displayed; install as user instead
- Working directory is not supported (LaunchServices limitation)
- This node is macOS-only; it will error if run on other platforms
- Requires Nodel v2.1.1-release365+
