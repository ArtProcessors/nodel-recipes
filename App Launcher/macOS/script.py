'''
**macOS App Launcher** - LaunchServices-based application launcher with foreground
activation, auto-restart with exponential backoff, and interruption detection.

`rev 9 2025.12.23`

## Features

1. LaunchServices launch via `open` command for reliable foreground behavior
2. Bundle ID auto-discovery from Info.plist
3. Foreground activation via AppleScript
4. Graceful quit via AppleScript with pkill fallback
5. Status polling every 5 seconds
6. Auto-restart with exponential backoff on crash
7. Interruption detection and status warnings

See `README.md` for expanded details.

**REVISION HISTORY**

* rev 9: refactored AppleScript helpers to reduce duplication
'''

# --- Parameters ---

param_AppPath = Parameter({
  'title': 'App Path (required)',
  'required': True,
  'schema': {'type': 'string', 'hint': 'e.g. /Applications/Safari.app'},
  'desc': 'Path to .app bundle or binary inside Contents/MacOS/'
})

param_AppArgs = Parameter({
  'title': 'App Arguments',
  'schema': {'type': 'string', 'hint': 'e.g. --debug --config "/path/to/config"'},
  'desc': 'Arguments passed via --args, space-delimited, backslash-escaped'
})

param_BundleID = Parameter({
  'title': 'Bundle ID',
  'schema': {'type': 'string', 'hint': 'e.g. com.apple.Safari'},
  'desc': 'macOS bundle identifier. Auto-discovered from Info.plist if not set.'
})

param_FullScreen = Parameter({
  'title': 'Full Screen',
  'schema': {'type': 'boolean'},
  'desc': 'Enter native macOS fullscreen mode after launch (covers dock and menu bar)'
})

param_PowerStateOnStart = Parameter({
  'title': 'Power State on Node Start',
  'schema': {'type': 'string', 'enum': ['On', 'Off', '(previous)']},
  'desc': 'What power state to use when the node starts'
})


# --- Signals ---

local_event_Running = LocalEvent({
  'group': 'Monitoring',
  'schema': {'type': 'string', 'enum': ['On', 'Off']},
  'desc': 'Actual running state of the application'
})

local_event_DesiredPower = LocalEvent({
  'group': 'Power',
  'schema': {'type': 'string', 'enum': ['On', 'Off']},
  'desc': 'The desired power state, set using the Power action'
})

local_event_Power = LocalEvent({
  'group': 'Power',
  'schema': {'type': 'string', 'enum': ['On', 'Partially On', 'Off', 'Partially Off']},
  'desc': 'Effective power state (combines desired and actual)'
})

local_event_PowerOn = LocalEvent({
  'group': 'Power',
  'title': 'On',
  'order': next_seq(),
  'schema': {'type': 'boolean'}
})

local_event_PowerOff = LocalEvent({
  'group': 'Power',
  'title': 'Off',
  'order': next_seq(),
  'schema': {'type': 'boolean'}
})

local_event_LastStarted = LocalEvent({
  'group': 'Monitoring',
  'schema': {'type': 'string'},
  'desc': 'Timestamp of last successful launch'
})

local_event_FirstInterrupted = LocalEvent({
  'group': 'Monitoring',
  'schema': {'type': 'string'},
  'desc': 'Timestamp of first interruption (resets on manual Power action)'
})

local_event_LastInterrupted = LocalEvent({
  'group': 'Monitoring',
  'schema': {'type': 'string'},
  'desc': 'Timestamp of most recent interruption'
})

local_event_Status = LocalEvent({
  'order': -100,
  'group': 'Status',
  'schema': {'type': 'object', 'properties': {
    'level': {'type': 'integer'},
    'message': {'type': 'string'}
  }}
})


# --- Persist signals aggressively ---

@after_main
def ensurePersistSignals():
  def ensure(s):
    s.addEmitHandler(lambda arg: s.persistNow())

  for s in [local_event_Running, local_event_DesiredPower, local_event_Power,
            local_event_LastStarted, local_event_FirstInterrupted, local_event_LastInterrupted]:
    ensure(s)


# --- Imports and OS check ---

import os

from java.lang import System as JavaSystem
_osName = JavaSystem.getProperty('os.name').lower()
_isMacOS = 'mac' in _osName or 'darwin' in _osName


# --- Global State ---

_bundlePath = None   # Path to .app bundle
_appName = None      # App name (e.g., "Safari")
_bundleId = None     # Bundle ID (e.g., "com.apple.Safari")

# Restart backoff configuration
RESTART_INITIAL_DELAY = 5       # First retry after 5 seconds
RESTART_MAX_DELAY = 300         # Max delay: 5 minutes
RESTART_MAX_ATTEMPTS = 10       # Give up after 10 consecutive failures
RESTART_STABLE_PERIOD = 60      # App must run 60s to reset backoff counter
RESTART_BACKOFF_MULTIPLIER = 2  # Double delay each failure

# Restart state tracking
_restartAttempts = 0
_lastRestartTime = None
_stableStartTime = None
_restartState = 'normal'  # 'normal', 'restarting', 'backoff', 'failed'
_permissionWarned = False  # Track if we've warned about AppleScript permissions


# --- Helper Functions ---

def _escapeAppleScriptString(s):
  """Escape a string for safe embedding in AppleScript."""
  if not s:
    return ''
  return s.replace('\\', '\\\\').replace('"', '\\"')

def _buildAppRef():
  """Return AppleScript app reference: 'application id "x"' or 'application "x"'."""
  if _bundleId:
    return 'application id "%s"' % _escapeAppleScriptString(_bundleId)
  return 'application "%s"' % _escapeAppleScriptString(_appName)

def _buildSystemEventsContains(prop, value):
  """Build AppleScript to check if a process property list contains value."""
  return '''tell application "System Events"
  set vals to %s of every process
  return vals contains "%s"
end tell''' % (prop, _escapeAppleScriptString(value))

def _buildStatusCheckScript():
  """Build AppleScript to check if app is running.

  Returns (script, checkType) where checkType is 'bundle' or 'name'.
  Returns (None, None) if no identifier available.
  """
  if _bundleId:
    return _buildSystemEventsContains('bundle identifier', _bundleId), 'bundle'
  if _appName:
    return _buildSystemEventsContains('name', _appName), 'name'
  return None, None

def _extractBundlePath(path):
  """Extract .app bundle path from a binary path inside Contents/MacOS/"""
  if '.app/Contents/MacOS/' in path:
    return path.split('.app/')[0] + '.app'
  elif path.endswith('.app'):
    return path
  return None

def _extractAppName(bundlePath):
  """Extract app name from bundle path (e.g., /Applications/Safari.app -> Safari)"""
  if bundlePath:
    return os.path.basename(bundlePath).replace('.app', '')
  return None

def _discoverBundleId(bundlePath, callback):
  """Discover Bundle ID using defaults read (async)"""
  plistPath = os.path.join(bundlePath, 'Contents', 'Info.plist')
  if not os.path.isfile(plistPath):
    callback(None)
    return

  def onResult(result):
    if result.code == 0 and result.stdout:
      callback(result.stdout.strip())
    else:
      callback(None)

  quick_process(['defaults', 'read', plistPath, 'CFBundleIdentifier'], finished=onResult)


# --- Launch/Stop Functions ---

def _launch():
  """Launch app via LaunchServices (open command)"""
  if not _bundlePath:
    console.warn('Launch skipped: app not initialized')
    return

  args = _decodeArgList(param_AppArgs) if not is_blank(param_AppArgs) else None

  def onLaunched(result):
    if result.code == 0:
      console.info('App launched via LaunchServices')
      local_event_Running.emit('On')
      local_event_LastStarted.emit(str(date_now()))

      # Always activate after launch (needed for fullscreen, good default behavior)
      call(_activate, 3)  # delay 3 sec for Electron apps to initialize
    else:
      console.error('Failed to launch: %s' % (result.stderr or result.stdout or 'unknown error'))

  if _bundleId:
    cmd = ['open', '-b', _bundleId]
  else:
    cmd = ['open', '-a', _bundlePath]

  if args:
    cmd.append('--args')
    cmd.extend(args)

  console.info('Launching: %s' % ' '.join(cmd))
  quick_process(cmd, finished=onLaunched)

_activateRetries = [0]  # Track retry count in list for closure access

def _activate():
  """Bring app to foreground using osascript with retry logic"""
  if not _appName:
    console.warn('Activation skipped: no appName')
    return

  # Wait for process to exist, then activate and set frontmost
  escapedName = _escapeAppleScriptString(_appName)
  script = '''tell application "System Events"
  repeat 10 times
    if exists process "%s" then exit repeat
    delay 0.5
  end repeat
end tell
tell %s to activate
delay 0.3
tell application "System Events"
  if exists process "%s" then
    set frontmost of process "%s" to true
  end if
end tell''' % (escapedName, _buildAppRef(), escapedName, escapedName)

  def onActivate(result):
    if result.code == 0:
      console.info('App activated (brought to foreground)')
      _activateRetries[0] = 0
      # Enter fullscreen if enabled (delay to let window settle)
      if param_FullScreen:
        call(_enterFullScreen, 1)
      # Kiosk mode: hide cursor
      call(_hideCursor, 2)
    else:
      # Retry up to 3 times with increasing delay
      if _activateRetries[0] < 3:
        _activateRetries[0] += 1
        console.warn('Activation attempt %s failed, retrying in 2s...' % _activateRetries[0])
        call(_activate, 2)
      else:
        console.warn('Activation failed after retries: %s' % (result.stderr or 'unknown error'))
        _activateRetries[0] = 0
        # Still try kiosk functions even if activation failed
        call(_hideCursor, 1)

  quick_process(['osascript', '-e', script], finished=onActivate)

def _enterFullScreen():
  """Enter native macOS fullscreen mode via accessibility API"""
  if not _appName:
    console.warn('Fullscreen skipped: no appName')
    return

  # Use AXFullScreen attribute via System Events
  script = '''
tell application "System Events"
  tell process "%s"
    if exists (first window) then
      set value of attribute "AXFullScreen" of window 1 to true
    end if
  end tell
end tell
''' % _appName

  def onFullScreen(result):
    if result.code == 0:
      console.info('App entered fullscreen mode')
    else:
      # Fallback: try Ctrl+Cmd+F keystroke
      console.warn('AXFullScreen failed, trying keystroke fallback...')
      _enterFullScreenKeystroke()

  quick_process(['osascript', '-e', script], finished=onFullScreen)

def _enterFullScreenKeystroke():
  """Fallback: enter fullscreen via Ctrl+Cmd+F keystroke"""
  script = '''
tell application "System Events"
  keystroke "f" using {control down, command down}
end tell
'''

  def onKeystroke(result):
    if result.code == 0:
      console.info('Fullscreen keystroke sent')
    else:
      console.warn('Fullscreen keystroke failed: %s' % (result.stderr or 'unknown error'))

  quick_process(['osascript', '-e', script], finished=onKeystroke)

def _hideCursor():
  """Move cursor to bottom-right corner off-screen (kiosk mode)"""
  # CGWarpMouseCursorPosition uses Quartz coords: (0,0) is top-left, Y increases downward
  script = '''
use framework "Foundation"
use framework "AppKit"
set screenFrame to current application's NSScreen's mainScreen()'s frame()
set screenWidth to item 1 of item 2 of screenFrame
set screenHeight to item 2 of item 2 of screenFrame
-- Move to bottom-right, off-screen (Quartz: Y increases downward)
set targetPoint to current application's NSMakePoint(screenWidth + 50, screenHeight + 50)
current application's CGWarpMouseCursorPosition(targetPoint)
'''
  quick_process(['osascript', '-e', script], finished=lambda r:
    console.info('Cursor hidden (bottom-right)') if r.code == 0 else console.warn('Hide cursor failed: %s' % r.stderr))

def _stop():
  """Stop app gracefully via osascript, fallback to pkill"""
  if not _appName:
    console.warn('Stop skipped: app not initialized')
    local_event_Running.emit('Off')
    return

  def onQuit(result):
    if result.code == 0:
      console.info('App quit gracefully')
      local_event_Running.emit('Off')
    else:
      console.warn('Graceful quit failed, attempting pkill fallback...')
      _forceKill()

  script = 'tell %s to quit' % _buildAppRef()
  quick_process(['osascript', '-e', script], finished=onQuit)

def _forceKill():
  """Force kill app - tries bundle ID match first, then app name"""

  def tryAppName():
    """Fallback: kill by app name"""
    def onKill(result):
      if result.code == 0:
        console.info('App force killed via pkill -x')
      else:
        console.warn('pkill failed (app may have already closed)')
      local_event_Running.emit('Off')

    quick_process(['pkill', '-x', _appName], finished=onKill)

  if _bundleId:
    # Try bundle ID first (more reliable for Electron apps)
    # Escape dots for regex (bundle IDs are like com.apple.Safari)
    pattern = _bundleId.replace('.', '\\.')

    def onBundleKill(result):
      if result.code == 0:
        console.info('App force killed via pkill -f (bundle ID)')
        local_event_Running.emit('Off')
      else:
        # Fall back to app name
        tryAppName()

    quick_process(['pkill', '-f', pattern], finished=onBundleKill)
  else:
    tryAppName()

def _isAppRunning(callback):
  """Check if app is running. Uses AppleScript (reliable for Electron), falls back to pgrep."""
  global _permissionWarned

  def fallbackToPgrep():
    """Fallback to pgrep -x when AppleScript fails"""
    global _permissionWarned
    if not _permissionWarned:
      _permissionWarned = True
      console.warn('AppleScript failed (permissions?) - falling back to pgrep for status detection')

    def onPgrep(result):
      callback(result.code == 0)

    quick_process(['pgrep', '-x', _appName], finished=onPgrep)

  script, checkType = _buildStatusCheckScript()
  if not script:
    callback(False)
    return

  def onResult(result):
    if result.code == 0:
      # AppleScript returns "true" or "false"
      isRunning = result.stdout.strip().lower() == 'true'
      callback(isRunning)
    else:
      # AppleScript failed - fall back to pgrep
      if _appName:
        fallbackToPgrep()
      else:
        callback(False)

  quick_process(['osascript', '-e', script], finished=onResult)


# --- Status Polling ---

def _pollStatus():
  """Poll running state every 5 seconds"""
  global _restartAttempts, _stableStartTime, _restartState

  if _bundleId is None and _appName is None:
    return  # Not initialized yet

  def onCheck(isRunning):
    global _restartAttempts, _stableStartTime, _restartState
    currentState = local_event_Running.getArg()
    desired = local_event_DesiredPower.getArg()
    now = date_now()

    if isRunning:
      if currentState != 'On':
        local_event_Running.emit('On')

      # Check if app has been stable long enough to reset backoff
      if _stableStartTime is None:
        _stableStartTime = now
      elif (now.getMillis() - _stableStartTime.getMillis()) > RESTART_STABLE_PERIOD * 1000:
        if _restartAttempts > 0:
          console.info('App stable for %ss, resetting restart counter' % RESTART_STABLE_PERIOD)
        _restartAttempts = 0
        _restartState = 'normal'

    else:
      _stableStartTime = None  # Reset stability timer

      if currentState != 'Off':
        local_event_Running.emit('Off')

      if desired == 'On':
        # App died while desired On - handle restart with backoff
        _handleInterruption(now)

  _isAppRunning(onCheck)

def _handleInterruption(now):
  """Handle app interruption with exponential backoff restart logic"""
  global _restartAttempts, _lastRestartTime, _restartState

  # Log interruption
  nowStr = str(now)
  local_event_LastInterrupted.emit(nowStr)
  if len(local_event_FirstInterrupted.getArg() or '') == 0:
    local_event_FirstInterrupted.emit(nowStr)

  # Check if we've exceeded max attempts
  if _restartAttempts >= RESTART_MAX_ATTEMPTS:
    if _restartState != 'failed':
      _restartState = 'failed'
      console.error('Max restart attempts (%s) exceeded - giving up' % RESTART_MAX_ATTEMPTS)
      console.error('Manual intervention required. Use Power Off then Power On to reset.')
    return

  # Calculate backoff delay
  delay = min(RESTART_INITIAL_DELAY * (RESTART_BACKOFF_MULTIPLIER ** _restartAttempts), RESTART_MAX_DELAY)

  # Check if we're still in backoff period
  if _lastRestartTime is not None:
    elapsedMs = now.getMillis() - _lastRestartTime.getMillis()
    if elapsedMs < delay * 1000:
      # Still in backoff, wait
      if _restartState != 'backoff':
        _restartState = 'backoff'
        console.info('In backoff period, next restart in %ss' % int((delay * 1000 - elapsedMs) / 1000))
      return

  # Attempt restart
  _restartAttempts += 1
  _lastRestartTime = now
  _restartState = 'restarting'
  nextDelay = min(delay * RESTART_BACKOFF_MULTIPLIER, RESTART_MAX_DELAY)
  console.warn('App interrupted - restart attempt %s/%s (next backoff: %ss)' %
               (_restartAttempts, RESTART_MAX_ATTEMPTS, nextDelay))
  _launch()

_statusTimer = Timer(_pollStatus, 5)


# --- Power Actions ---

@local_action({
  'group': 'Power',
  'order': next_seq(),
  'schema': {'type': 'string', 'enum': ['On', 'Off']},
  'desc': 'Control app power state. Also clears interruption warnings and resets restart backoff.'
})
def Power(arg):
  global _restartAttempts, _restartState, _stableStartTime, _lastRestartTime

  # Reset restart state on manual power action
  local_event_FirstInterrupted.emit('')
  _restartAttempts = 0
  _restartState = 'normal'
  _stableStartTime = None
  _lastRestartTime = None

  if arg == 'On':
    local_event_DesiredPower.emit('On')
    _launch()

  elif arg == 'Off':
    local_event_DesiredPower.emit('Off')
    _stop()

@local_action({'group': 'Power', 'title': 'On', 'order': next_seq()})
def PowerOn():
  Power.call('On')

@local_action({'group': 'Power', 'title': 'Off', 'order': next_seq()})
def PowerOff():
  Power.call('Off')


# --- Power State Logic ---

@before_main
def initRunningState():
  local_event_Running.emit('Off')

def determinePower(arg):
  desired = local_event_DesiredPower.getArg()
  running = local_event_Running.getArg()

  if desired is None:
    state = running
  elif desired == running:
    state = running
  else:
    state = 'Partially %s' % desired

  local_event_Power.emit(state)
  local_event_PowerOn.emit(running == 'On')
  local_event_PowerOff.emit(running == 'Off')

@after_main
def bindPower():
  local_event_Running.addEmitHandler(determinePower)
  local_event_DesiredPower.addEmitHandler(determinePower)


# --- Lifecycle ---

def main():
  if not _isMacOS:
    console.error('This node is macOS-only. Running on: %s' % _osName)
    return

  if is_blank(param_AppPath):
    console.error('No App Path specified')
    return

  global _bundlePath, _appName, _bundleId

  # Extract bundle path from the provided path
  _bundlePath = _extractBundlePath(param_AppPath)

  if _bundlePath is None:
    # Assume the path itself is the bundle
    _bundlePath = param_AppPath

  if not os.path.isdir(_bundlePath):
    console.error('App bundle not found: %s' % _bundlePath)
    return

  _appName = _extractAppName(_bundlePath)
  _bundleId = param_BundleID  # May be None

  console.info('Bundle path: %s' % _bundlePath)
  console.info('App name: %s' % _appName)

  # Auto-discover bundle ID if not provided
  if is_blank(_bundleId):
    def onBundleIdDiscovered(discoveredId):
      global _bundleId
      if discoveredId:
        _bundleId = discoveredId
        console.info('Bundle ID (auto-discovered): %s' % _bundleId)
      else:
        console.info('Bundle ID not found, using app name for activation')
      _initPowerState()

    _discoverBundleId(_bundlePath, onBundleIdDiscovered)
  else:
    console.info('Bundle ID (provided): %s' % _bundleId)
    _initPowerState()

def _initPowerState():
  """Initialize power state based on configuration"""
  console.info('Interruption detection enabled (status polling every 5s)')
  console.info('Auto-restart with exponential backoff enabled')

  if param_PowerStateOnStart == 'On':
    lookup_local_action('Power').call('On')
  elif param_PowerStateOnStart == 'Off':
    lookup_local_action('Power').call('Off')
  else:
    # (previous) - check what the desired power was
    if local_event_DesiredPower.getArg() == 'On':
      console.info('Resuming previous power state: On')
      _launch()  # DesiredPower already 'On' from persistence
    else:
      console.info('Previous power state was Off, not starting')


# --- Status Check ---

def statusCheck():
  # Check restart state first (highest priority)
  if _restartState == 'failed':
    local_event_Status.emit({
      'level': 2,
      'message': 'App restart failed after %s attempts - manual intervention required' % RESTART_MAX_ATTEMPTS
    })
    return

  if _restartState == 'backoff':
    local_event_Status.emit({
      'level': 1,
      'message': 'App restarting (attempt %s/%s)' % (_restartAttempts, RESTART_MAX_ATTEMPTS)
    })
    return

  # Check for recent interruptions (within 4 days)
  now = date_now()
  nowMillis = now.getMillis()

  firstInterrupted = date_parse(local_event_FirstInterrupted.getArg() or '1960')
  firstInterruptedDiff = nowMillis - firstInterrupted.getMillis()

  lastInterrupted = date_parse(local_event_LastInterrupted.getArg() or '1960')

  if firstInterruptedDiff < 4*24*3600*1000L:  # 4 days
    if firstInterrupted.getMillis() == lastInterrupted.getMillis():
      timeMsgs = 'last time %s' % _toBriefTime(lastInterrupted)
    else:
      timeMsgs = 'last time %s, first time %s' % (_toBriefTime(lastInterrupted), _toBriefTime(firstInterrupted))

    local_event_Status.emit({
      'level': 1,
      'message': 'Application interruptions detected (%s)' % timeMsgs
    })
    return

  # Check if app should be running but isn't
  if local_event_DesiredPower.getArg() == 'On' and local_event_Running.getArg() != 'On':
    local_event_Status.emit({'level': 2, 'message': 'Application is not running'})
    return

  local_event_Status.emit({'level': 0, 'message': 'OK'})

_statusCheckTimer = Timer(statusCheck, 30)


# --- Utilities ---

def _toBriefTime(dateTime):
  """Convert timestamp to brief human-readable time"""
  now = date_now()
  nowMillis = now.getMillis()

  diff = (nowMillis - dateTime.getMillis()) / 60000  # in minutes

  if diff == 0:
    return '<1 min ago'
  elif diff < 60:
    return '%s mins ago' % diff
  elif diff < 24*60:
    return dateTime.toString('h:mm:ss a')
  elif diff < 365 * 24*60:
    return dateTime.toString('h:mm:ss a, E d-MMM')
  elif diff > 10 * 365*24*60:
    return 'never'
  else:
    return '>1 year'

def _decodeArgList(argsString):
  """
  Decode a process arg list string into an array of strings.
  Supports backslash escaping and double-quote grouping.

  Example: --name "Peter Parker" --hero Spider\ Man
  Returns: ['--name', 'Peter Parker', '--hero', 'Spider Man']
  """
  argsList = []
  escaping = False
  quoting = False
  currentArg = []

  for c in argsString:
    # Handle escape: next char is literal
    if escaping:
      escaping = False
      currentArg.append(c)  # Append escaped char only (not the backslash)
      continue

    if c == '\\':
      escaping = True
      continue

    # Handle quotes: toggle quoting mode, don't include quote char
    if c == '"':
      quoting = not quoting
      continue

    # Handle spaces: delimiter when not quoting
    if c == ' ' and not quoting:
      if currentArg:
        argsList.append(''.join(currentArg))
        currentArg = []
      continue

    # Regular character
    currentArg.append(c)

  # Trailing arg
  if currentArg:
    argsList.append(''.join(currentArg))

  return argsList
