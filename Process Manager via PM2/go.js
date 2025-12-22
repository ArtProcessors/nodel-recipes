// PM2 Controller - Node.js Bridge
// Communicates with Nodel via JSON over stdin/stdout
// Uses PM2's programmatic API for process management

const pm2 = require('pm2');
const readline = require('readline');

// === STATE ===
let config = null;       // Process configuration from Nodel
let processName = null;  // PM2 process name
let bus = null;          // PM2 event bus
let logStreaming = false;
let bridgeConnected = false;  // Track if we've emitted BridgeConnected

// === POLLING ===
const FAST_POLL_MS = 2000;   // 2 seconds after events
const SLOW_POLL_MS = 30000;  // 30 seconds when stable
const FAST_POLL_COUNT = 15;  // Number of fast polls before slowing (~30 sec of fast polling)

let pollTimer = null;
let fastPollsRemaining = 0;

// === HELPERS ===
function emit(event, arg) {
  console.log(JSON.stringify({ event, arg }));
}

function emitLog(type, data) {
  console.log(JSON.stringify({ log: type, data }));
}

function log(msg) {
  console.log('# ' + msg);
}

// Trigger fast polling (after significant events)
function triggerFastPoll() {
  fastPollsRemaining = FAST_POLL_COUNT;
  schedulePoll();
}

// Schedule next poll based on current state
function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer);
  if (!processName) return;  // Don't poll if not configured

  const interval = fastPollsRemaining > 0 ? FAST_POLL_MS : SLOW_POLL_MS;
  pollTimer = setTimeout(() => {
    if (fastPollsRemaining > 0) fastPollsRemaining--;
    refreshStatus();
    schedulePoll();
  }, interval);
}

// === METADATA (Dynamic Reflection) ===
// Declare events that go.js will emit
const events = [
  { name: 'Running', metadata: { group: 'Power', order: 1, schema: { type: 'string', enum: ['On', 'Off'] } } },
  { name: 'PM2Status', metadata: { group: 'PM2 Status', order: 2, schema: { type: 'string' } } },
  { name: 'ProcessId', metadata: { group: 'PM2 Status', title: 'PM2 ID', order: 3, schema: { type: 'integer' } } },
  { name: 'PID', metadata: { group: 'PM2 Status', title: 'OS PID', order: 4, schema: { type: 'integer' } } },
  { name: 'Uptime', metadata: { group: 'PM2 Status', order: 5, schema: { type: 'integer' }, desc: 'Uptime in milliseconds' } },
  { name: 'CPU', metadata: { group: 'Metrics', order: 10, schema: { type: 'number' }, desc: 'CPU percentage' } },
  { name: 'Memory', metadata: { group: 'Metrics', order: 11, schema: { type: 'integer' }, desc: 'Memory in bytes' } },
  { name: 'RestartCount', metadata: { group: 'Metrics', title: 'Restart Count', order: 12, schema: { type: 'integer' } } },
  { name: 'BridgeConnected', metadata: { group: 'Lifecycle', title: 'Bridge Connected', order: 20, schema: { type: 'boolean' } } },
  { name: 'Error', metadata: { group: 'Status', order: 100, schema: { type: 'string' } } }
];

// Declare actions that go.js will handle
const actions = [
  { name: 'Power', metadata: { group: 'Power', order: 1, schema: { type: 'string', enum: ['On', 'Off'] } } },
  { name: 'PowerOn', metadata: { group: 'Power', title: 'On', order: 2 } },
  { name: 'PowerOff', metadata: { group: 'Power', title: 'Off', order: 3 } },
  { name: 'Restart', metadata: { group: 'PM2 Control', order: 10 } },
  { name: 'Delete', metadata: { group: 'PM2 Control', order: 11, desc: 'Stop and remove from PM2' } },
  { name: 'RefreshStatus', metadata: { group: 'PM2 Control', title: 'Refresh Status', order: 20, desc: 'Force status refresh' } }
];

// Emit metadata on startup
console.log(JSON.stringify({ events }));
console.log(JSON.stringify({ actions }));

// === PM2 STATUS ===
function refreshStatus() {
  if (!processName) {
    emit('Running', 'Off');
    emit('PM2Status', 'not configured');
    return;
  }

  pm2.list((err, list) => {
    if (err) {
      emit('Error', err.message);
      return;
    }

    // Emit BridgeConnected on first successful PM2 operation
    if (!bridgeConnected) {
      bridgeConnected = true;
      log('PM2 bridge connected');
      emit('BridgeConnected', true);
    }

    const proc = list.find(p => p.name === processName);

    if (!proc) {
      emit('Running', 'Off');
      emit('PM2Status', 'stopped');
      return;
    }

    const status = proc.pm2_env?.status || 'unknown';
    const uptime = proc.pm2_env?.pm_uptime
      ? Date.now() - proc.pm2_env.pm_uptime
      : 0;

    emit('Running', status === 'online' ? 'On' : 'Off');
    emit('PM2Status', status);
    emit('ProcessId', proc.pm_id);
    emit('PID', proc.pid || 0);
    emit('Uptime', uptime);
    emit('CPU', Math.round((proc.monit?.cpu || 0) * 10) / 10);
    emit('Memory', proc.monit?.memory || 0);
    emit('RestartCount', proc.pm2_env?.restart_time || 0);
  });
}

// === PM2 BUS (Real-time Events) ===
function setupBus() {
  pm2.launchBus((err, pm2Bus) => {
    if (err) {
      log('Failed to launch bus: ' + err.message);
      return;
    }

    bus = pm2Bus;
    log('PM2 bus connected');

    // Process lifecycle events
    bus.on('process:event', (data) => {
      if (!processName || data.process?.name !== processName) return;
      log('Process event: ' + data.event);
      refreshStatus();
      triggerFastPoll();  // Fast poll after lifecycle events
    });

    // Log streaming (stdout)
    bus.on('log:out', (data) => {
      if (!logStreaming || !processName) return;
      if (data.process?.name !== processName) return;
      emitLog('out', data.data);
    });

    // Log streaming (stderr)
    bus.on('log:err', (data) => {
      if (!logStreaming || !processName) return;
      if (data.process?.name !== processName) return;
      emitLog('err', data.data);
    });
  });
}

// === ACTIONS ===
function handleAction(action, arg) {
  log('Action: ' + action + ', arg: ' + JSON.stringify(arg));

  switch (action) {
    case 'Power':
      if (arg === 'On') startProcess();
      else if (arg === 'Off') stopProcess();
      break;

    case 'PowerOn':
      startProcess();
      break;

    case 'PowerOff':
      stopProcess();
      break;

    case 'Restart':
      restartProcess();
      break;

    case 'Delete':
      deleteProcess();
      break;

    case 'RefreshStatus':
      refreshStatus();
      break;

    default:
      log('Unknown action: ' + action);
  }
}

function startProcess() {
  if (!config) {
    emit('Error', 'No configuration received');
    return;
  }

  log('Starting process: ' + processName);

  // Build PM2 start options
  const options = {
    name: processName,
    script: config.script
  };

  if (config.args) options.args = config.args;
  if (config.cwd) options.cwd = config.cwd;
  if (config.maxMemory) options.max_memory_restart = config.maxMemory;
  if (config.maxRestarts !== undefined) options.max_restarts = config.maxRestarts;
  if (config.autoRestart !== undefined) options.autorestart = config.autoRestart;
  if (config.watch !== undefined) options.watch = config.watch;
  if (config.env) options.env = config.env;

  pm2.start(options, (err, proc) => {
    if (err) {
      emit('Error', 'Start failed: ' + err.message);
      log('Start error: ' + err.message);
    } else {
      log('Process started');
    }
    refreshStatus();
    triggerFastPoll();  // Fast poll after start
  });
}

function stopProcess() {
  if (!processName) return;
  log('Stopping process: ' + processName);

  pm2.stop(processName, (err) => {
    if (err) {
      emit('Error', 'Stop failed: ' + err.message);
      log('Stop error: ' + err.message);
    } else {
      log('Process stopped');
    }
    refreshStatus();
    triggerFastPoll();  // Fast poll after stop
  });
}

function restartProcess() {
  if (!processName) return;
  log('Restarting process: ' + processName);

  pm2.restart(processName, (err) => {
    if (err) {
      emit('Error', 'Restart failed: ' + err.message);
    } else {
      log('Process restarted');
    }
    refreshStatus();
    triggerFastPoll();  // Fast poll after restart
  });
}

function deleteProcess() {
  if (!processName) return;
  log('Deleting process: ' + processName);

  pm2.delete(processName, (err) => {
    if (err) {
      emit('Error', 'Delete failed: ' + err.message);
    } else {
      log('Process deleted');
    }
    refreshStatus();
    triggerFastPoll();  // Fast poll after delete
  });
}

// === CONFIGURATION ===
function handleConfig(cfg) {
  config = cfg;
  processName = cfg.name;
  logStreaming = cfg.logStreaming || false;

  log('Configuration received: ' + JSON.stringify(cfg));

  // Check if process already exists in PM2
  refreshStatus();

  // Start slow polling for metrics
  schedulePoll();
}

// === STDIN HANDLER ===
function handleCommand(line) {
  if (line.startsWith('#')) return; // Ignore comments

  try {
    const msg = JSON.parse(line);

    if (msg.config) {
      handleConfig(msg.config);
    } else if (msg.action) {
      handleAction(msg.action, msg.arg);
    }
  } catch (e) {
    log('Failed to parse command: ' + e.message);
  }
}

// === MAIN ===
log('PM2 Controller bridge starting...');

pm2.connect((err) => {
  if (err) {
    emit('Error', 'PM2 connect failed: ' + err.message);
    emit('BridgeConnected', false);
    log('PM2 connect error: ' + err.message);
    process.exit(1);
  }

  // Setup event bus for real-time updates
  setupBus();
});

// Listen for commands from Nodel
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', handleCommand);

// Handle shutdown
process.on('SIGINT', () => {
  log('Shutting down...');
  pm2.disconnect();
  process.exit(0);
});
