# macOS Computer Controller

A macOS-specific Computer Controller node with deep OS integration via a Swift companion process.

`REV 1.20251222`

## Features

- **Audio Control**: Volume, mute, audio metering, output device info
- **Power Management**: Sleep, prevent sleep, power settings (WoL, auto-restart, etc.)
- **Screenshots**: Periodic multi-display capture with thumbnails
- **System Monitoring**: CPU usage, hardware info, disk space

## Requirements

### Xcode Command Line Tools

The Swift companion is compiled on first run. Install the tools if not present:

```bash
xcode-select --install
```

### Permissions

**Screen Recording** is required for screenshots:

1. Open **System Settings > Privacy & Security > Screen Recording**
2. Enable the terminal/Java process running Nodel
3. Restart the node

The node will emit `PermissionNeeded` events and console warnings if permissions are missing.

### Elevated Commands Setup (Optional)

Some actions require administrator privileges to modify system settings:
- **Power Settings**: WakeOnLAN, AutoRestartOnPowerLoss, WakeForNetworkAccess, PowerNap
- **Unattended Power**: ShutdownUnattended, RestartUnattended

Without setup, these actions will log warnings and have no effect.

**One-time setup:**
```bash
cd /path/to/nodes/Computer\ Controller\ macOS\ Phobos
sudo ./install-sudoers.sh
```

This installs `/etc/sudoers.d/nodel-mac-controller` allowing passwordless execution of specific pmset and shutdown commands.

**To remove:**
```bash
sudo rm /etc/sudoers.d/nodel-mac-controller
```

## Comparison with Windows Version

| Feature | Windows | macOS |
|---------|---------|-------|
| Volume Control | Yes (dB + scalar) | Yes (dB + scalar) |
| Mute | Yes | Yes |
| Audio Meter | Yes | Yes |
| CPU Monitoring | Yes | Yes |
| Screenshots | Yes | Yes (requires permission) |
| Hardware Info | Yes (WMI) | Yes (sysctl) |
| Lock Screen | Yes | Yes |
| Sleep | Yes (Suspend) | Yes |
| Shutdown/Restart | Yes | GUI prompt or unattended (with sudoers) |
| Prevent Sleep | No | Yes (caffeinate) |

## How It Works

1. On first run, `script.py` compiles `MacController.swift` using `swiftc`
2. The compiled binary runs as a subprocess managed by Nodel's `Process` toolkit
3. Commands are sent via stdin, events received via stdout as JSON
4. Optional polling uses Nodel timers (CPU, volume, screenshots) and is off by default

## Actions

| Action | Group | Description |
|--------|-------|-------------|
| Volume | Volume | Set volume 0-100 |
| Mute | Volume | Set mute on/off |
| MuteOn | Volume | Mute audio |
| MuteOff | Volume | Unmute audio |
| Wake | Power | Wake display from sleep |
| Sleep | Power | Put system to sleep |
| PreventSleep | Power Settings | Toggle caffeinate |
| WakeOnLAN | Power Settings | Toggle Wake on LAN (pmset womp) |
| AutoRestartOnPowerLoss | Power Settings | Toggle auto-restart after power loss (pmset autorestart) |
| WakeForNetworkAccess | Power Settings | Toggle wake for network access (pmset networkoversleep) |
| PowerNap | Power Settings | Toggle Power Nap (pmset powernap) |
| PowerOff | Power | Sleep (WOL compatible) or Shutdown based on parameter |
| Shutdown | Power | Full shutdown (not WOL recoverable) |
| Restart | Power | Restart the system |
| ShutdownUnattended | Power | Immediate shutdown (requires sudoers setup) |
| RestartUnattended | Power | Immediate restart (requires sudoers setup) |

## Events

| Event | Group | Description |
|-------|-------|-------------|
| Volume | Volume | Current volume (0-100) |
| Mute | Volume | Mute state |
| AudioMeter | Volume | Audio peak level (dB) |
| OutputDevice | Volume | Current output device name |
| CPU | - | CPU usage percentage |
| Screenshot1, Screenshot2... | Screenshots | Base64 JPEG thumbnails |
| CPUName | Hardware Info | Processor name |
| Model | Hardware Info | Mac model identifier |
| Cores | Hardware Info | Physical CPU cores |
| LogicalProcessors | Hardware Info | Logical processors |
| PhysicalMemory | Hardware Info | RAM size |
| Architecture | Hardware Info | arm64 or x86_64 |
| WakeOnLAN | Power Settings | Wake on LAN enabled (pmset womp) |
| AutoRestartOnPowerLoss | Power Settings | Auto-restart after power loss enabled (pmset autorestart) |
| WakeForNetworkAccess | Power Settings | Wake for network access enabled (pmset networkoversleep) |
| PowerNap | Power Settings | Power Nap enabled (pmset powernap) |
| Status | Status | Disk space warnings |

## Parameters

| Parameter | Description |
|-----------|-------------|
| Screenshot Interval (s) | How often to capture screenshots (default 60) |
| Freespace Threshold (GB) | Disk space warning threshold (default 0.5) |

## Troubleshooting

### "Swift compiler not found"
Install Xcode Command Line Tools: `xcode-select --install`

### Screenshots not working
Grant Screen Recording permission in System Settings and restart the node.

### Volume control not working
Ensure an audio output device is available. Check console for CoreAudio errors.

### Shutdown/Restart requires interaction
These actions use AppleScript which prompts the user. For unattended operation, configure sudoers or use alternative methods.

### Power Settings actions do not change system settings
The Power Settings actions use `pmset -a`, which requires root. Without elevated privileges the actions fire, but the system settings do not change and logs will show a permissions error.

Workarounds:
- Run Nodel as root (simple but broad privileges).
- Use a sudoers allow-list so the Nodel user can run `pmset` without a password.
- Package a privileged helper using SMJobBless to perform `pmset` calls.

### Sleep not working (system stays awake)

Several things can prevent macOS from sleeping:

**1. Check power assertions:**
```bash
pmset -g assertions
```

Common blockers:
- `caffeinate` - KeepingYouAwake app or manual caffeinate commands
- `screensharingd` - Active screen sharing session
- `PreventSystemSleep` - Various apps holding sleep assertions

**2. TTY keeping system awake:**

By default, active terminal sessions (including Nodel's Java process) prevent sleep:
```bash
# Check current setting
pmset -g | grep ttyskeepawake

# Disable (allows sleep even with active terminals)
sudo pmset -a ttyskeepawake 0
```

**3. Kill caffeinate processes:**

The PowerOff action automatically kills caffeinate, but you can do it manually:
```bash
pkill caffeinate
```

### Wake from sleep goes to lock screen

To skip the lock screen after wake (for unattended operation), use `sysadminctl`:

```bash
# Disable screen lock (requires user password)
sysadminctl -screenLock off -password "yourpassword"

# Verify it's disabled
sysadminctl -screenLock status
# Should show: screenLock is off
```

Or via **System Settings > Lock Screen > "Require password after screen saver begins or display is off"** → Never

**Note:** The `defaults write com.apple.screensaver askForPassword` approach does NOT work on modern macOS - you must use `sysadminctl`.

### Wake-on-LAN not working

**1. Verify WOL is enabled:**
```bash
pmset -g | grep womp
# Should show: womp 1
```

If disabled, enable it:
```bash
sudo pmset -a womp 1
```

**2. WOL only works from sleep, not shutdown:**

macOS WOL requires the system to be in sleep state, not fully powered off. Use the `PowerOff` action with "Sleep" mode (default), not "Shutdown".

**3. Check network interface supports WOL:**
```bash
pmset -g assertions | grep MAGICWAKE
# Should show entries for en0/en1
```

### Waking the display programmatically

If the display is sleeping but system is awake:
```bash
caffeinate -u -t 1
```
