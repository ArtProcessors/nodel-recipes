# PM2 Controller

A Nodel recipe for managing long-running applications through [PM2](https://pm2.keymetrics.io/).

Designed for museum interactives and kiosk applications - Electron apps, Unity apps, MadMapper, TouchDesigner, VLC, etc.

## Prerequisites

### PM2

PM2 must be installed globally on the system. Install via npm or bun:

```bash
# Using npm
npm install pm2 -g

# Using bun
bun install pm2 -g
```

See [PM2 on npm](https://www.npmjs.com/package/pm2) for more details.

### Node.js

Node.js must be available on the system PATH (used to run the bridge script).

## How It Works

This recipe uses a Node.js bridge (`go.js`) that communicates with PM2's daemon via its programmatic API. The bridge:

1. Connects to the PM2 daemon (started automatically by PM2)
2. Receives real-time process events via PM2's event bus
3. Exposes PM2 controls as Nodel actions (Power, Restart, Delete)
4. Emits process status as Nodel events (Running, CPU, Memory, etc.)

On first run, the recipe installs the `pm2` npm package locally (in its own `node_modules/`) so the Node.js bridge can use the API. This is separate from your global PM2 installation but connects to the same daemon.

## Parameters

| Parameter | Description |
|-----------|-------------|
| **Application Path** | Full path to the executable (required) |
| **PM2 Process Name** | Name in PM2 (defaults to Nodel node name) |
| **Script Arguments** | Command-line arguments for the app |
| **Working Directory** | Working directory for the process |
| **Max Memory Restart** | Restart if memory exceeds limit (e.g. "500M") |
| **Max Restarts** | Stop retrying after N consecutive crashes |
| **Auto Restart** | Restart on crash (default: true) |
| **Watch for Changes** | Restart when files change |
| **Environment Variables** | Environment variables to set |
| **Running state on Node Start** | On / Off / (previous) |
| **Stream Logs to Console** | Show app output in Nodel console |

## Events

| Event | Description |
|-------|-------------|
| **Running** | On / Off |
| **Power** | On / Partially On / Off / Partially Off |
| **PM2Status** | online / stopped / errored / etc. |
| **CPU** | CPU percentage |
| **Memory** | Memory usage in bytes |
| **RestartCount** | Number of restarts |
| **PID** | OS process ID |

## Actions

| Action | Description |
|--------|-------------|
| **Power** | Start or stop the application |
| **PowerOn / PowerOff** | Convenience actions |
| **Restart** | Restart the application |
| **Delete** | Stop and remove from PM2 |
| **RefreshStatus** | Force a status refresh |

## Example: VLC Kiosk

```json
{
  "paramValues": {
    "AppPath": "C:\\Program Files\\VideoLAN\\VLC\\vlc.exe",
    "Args": "C:\\content\\video.mp4 --fullscreen --loop",
    "PowerStateOnStart": "On",
    "AutoRestart": true
  }
}
```
