import Foundation
import CoreAudio
import Darwin.Mach
import AppKit
import ScreenCaptureKit
import AVFoundation
import CoreMedia
import SystemConfiguration

// MARK: - Constants

// kAudioHardwareServiceDeviceProperty_VirtualMainVolume = 'vmvc'
let kVirtualMainVolume: AudioObjectPropertySelector = 0x766D7663

// MARK: - Main

var running = true

// MARK: - Audio Metering State

var audioMeterActive = false
var audioMeterPollingActive = false

var audioStream: SCStream?
var audioDelegate: AudioMeterDelegate?
var currentPeakDB: Float = -60.0  // -60 dB = silence (minimum)
var lastAudioSampleTime: Date = Date.distantPast

// MARK: - Audio Meter Delegate

class AudioMeterDelegate: NSObject, SCStreamOutput {
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        // Only process audio samples
        guard type == .audio else { return }

        processSamples(sampleBuffer)
    }

    private func processSamples(_ sampleBuffer: CMSampleBuffer) {
        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        var length = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        let status = CMBlockBufferGetDataPointer(blockBuffer, atOffset: 0, lengthAtOffsetOut: nil,
                                                  totalLengthOut: &length, dataPointerOut: &dataPointer)

        guard status == kCMBlockBufferNoErr, let data = dataPointer, length > 0 else { return }

        // Get audio format to determine sample type
        guard let formatDesc = CMSampleBufferGetFormatDescription(sampleBuffer) else { return }
        guard let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(formatDesc)?.pointee else { return }

        var peak: Float = 0

        if asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0 {
            // Float32 samples
            let floatCount = length / MemoryLayout<Float32>.size
            let floatPointer = UnsafeRawPointer(data).bindMemory(to: Float32.self, capacity: floatCount)

            for i in 0..<floatCount {
                peak = max(peak, abs(floatPointer[i]))
            }
        } else if asbd.mBitsPerChannel == 16 {
            // Int16 samples - convert to float
            let sampleCount = length / MemoryLayout<Int16>.size
            let int16Pointer = UnsafeRawPointer(data).bindMemory(to: Int16.self, capacity: sampleCount)

            for i in 0..<sampleCount {
                let normalized = Float(int16Pointer[i]) / Float(Int16.max)
                peak = max(peak, abs(normalized))
            }
        }

        // Convert to dB (with floor at -60 dB)
        let db: Float = peak > 0.0001 ? 20 * log10(peak) : -60
        currentPeakDB = max(-60, min(0, db))
        lastAudioSampleTime = Date()
    }
}

// MARK: - Audio Meter Setup

func startAudioMeter() {
    SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: false) { content, error in
        if let error = error {
            print("{ \"event\": \"PermissionNeeded\", \"arg\": \"Screen Recording (for audio capture)\" }")
            return
        }

        guard let content = content, let display = content.displays.first else {
            print("// Audio metering setup failed: no displays")
            return
        }

        // Dispatch to main thread - ScreenCaptureKit requires main RunLoop for callbacks
        DispatchQueue.main.async {
            setupAudioStream(display: display)
        }
    }
}

func setupAudioStream(display: SCDisplay) {
    // Configure stream for audio capture (minimize video)
    let config = SCStreamConfiguration()
    config.capturesAudio = true
    config.excludesCurrentProcessAudio = false
    config.sampleRate = 48000
    config.channelCount = 2

    // Minimize video overhead - we only want audio
    config.width = 2
    config.height = 2
    config.minimumFrameInterval = CMTime(value: 1, timescale: 1) // 1 FPS minimum
    config.showsCursor = false

    // Create filter for the display
    let filter = SCContentFilter(display: display, excludingWindows: [])

    // Create stream
    let stream = SCStream(filter: filter, configuration: config, delegate: nil)

    // Create and set delegate
    let delegate = AudioMeterDelegate()
    audioDelegate = delegate

    do {
        // Add audio output
        try stream.addStreamOutput(delegate, type: .audio, sampleHandlerQueue: DispatchQueue.global(qos: .userInteractive))

        stream.startCapture { captureError in
            if let captureError = captureError {
                print("{ \"event\": \"PermissionNeeded\", \"arg\": \"Screen Recording (for audio capture)\" }")
            } else {
                // Race condition fix: check if still active before assigning
                guard audioMeterActive else {
                    stream.stopCapture { _ in }
                    return
                }
                audioStream = stream
            }
        }
    } catch {
        print("{ \"event\": \"PermissionNeeded\", \"arg\": \"Screen Recording (for audio capture)\" }")
    }
}

func stopAudioMeter() {
    if let stream = audioStream {
        stream.stopCapture { error in
            if let error = error {
                print("// Error stopping audio stream: \(error)")
            } else {
                print("// Audio metering stopped")
            }
        }
        audioStream = nil
        audioDelegate = nil
    }
}

func startAudioMetering() {
    guard !audioMeterActive else { return }
    audioMeterActive = true
    currentPeakDB = -60
    lastEmittedPeakDB = -60
    lastAudioSampleTime = Date.distantPast
    startAudioMeter()
    startAudioMeterPolling()
}

func stopAudioMetering() {
    audioMeterActive = false
    audioMeterPollingActive = false
    stopAudioMeter()
    lastEmittedPeakDB = -60
}

func main() {
    print("// macOS Controller started")

    // Process stdin commands on a background thread
    // This keeps the main RunLoop free for ScreenCaptureKit callbacks
    DispatchQueue.global(qos: .userInteractive).async {
        processStdin()
    }

    // Run the main RunLoop indefinitely for ScreenCaptureKit callbacks
    // CFRunLoopRun() is more suitable for continuous audio callbacks
    CFRunLoopRun()
}

// MARK: - Command Processing

func processStdin() {
    while running {
        guard let line = readLine()?.trimmingCharacters(in: .whitespaces) else {
            // stdin closed - exit the loop to avoid hot-spinning
            shutdown()
            break
        }

        if line.isEmpty { continue }

        let parts = line.split(separator: " ", maxSplits: 1).map { String($0) }
        let command = parts[0]
        let arg = parts.count > 1 ? parts[1] : nil

        switch command {
        case "get-volume":
            emitVolume()
        case "set-volume":
            if let val = arg.flatMap({ Double($0) }) {
                setVolume(Float(val))
                emitVolume()
            }
        case "get-mute":
            emitMute()
        case "set-mute":
            if let val = arg {
                setMute(val == "true")
                emitMute()
            }
        case "list-devices":
            emitOutputDevices()
        case "screenshot":
            takeScreenshots()
        case "get-cpu":
            emitCPU()
        case "emit-hardware-info":
            emitHardwareInfo()
        case "start-audio-meter":
            startAudioMetering()
        case "stop-audio-meter":
            stopAudioMetering()
        case "emit-mac-addresses":
            emitMACAddresses()
        case "q":
            print("// Goodbye")
            shutdown()
        default:
            print("// Unknown command: \(command)")
        }
    }
}

func parseBoolArg(_ value: String) -> Bool? {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    if ["true", "1", "on", "yes"].contains(normalized) {
        return true
    }
    if ["false", "0", "off", "no"].contains(normalized) {
        return false
    }
    return nil
}

// MARK: - Audio Control

func getDefaultOutputDevice() -> AudioDeviceID? {
    var deviceID = AudioDeviceID()
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )

    let status = AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject),
        &address,
        0, nil,
        &size, &deviceID
    )

    return status == noErr ? deviceID : nil
}

func getVolume() -> Float? {
    guard let deviceID = getDefaultOutputDevice() else { return nil }

    var volume: Float32 = 0
    var size = UInt32(MemoryLayout<Float32>.size)

    // Try virtual main volume first
    var address = AudioObjectPropertyAddress(
        mSelector: kVirtualMainVolume,
        mScope: kAudioDevicePropertyScopeOutput,
        mElement: kAudioObjectPropertyElementMain
    )

    var status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &volume)

    if status != noErr {
        // Fall back to channel 1 volume scalar
        address.mSelector = kAudioDevicePropertyVolumeScalar
        address.mElement = 1
        status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &volume)
    }

    return status == noErr ? volume : nil
}

func setVolume(_ volume: Float) {
    guard let deviceID = getDefaultOutputDevice() else { return }

    var vol = max(0, min(1, volume))

    // Try virtual main volume first
    var address = AudioObjectPropertyAddress(
        mSelector: kVirtualMainVolume,
        mScope: kAudioDevicePropertyScopeOutput,
        mElement: kAudioObjectPropertyElementMain
    )

    var status = AudioObjectSetPropertyData(
        deviceID, &address, 0, nil,
        UInt32(MemoryLayout<Float32>.size), &vol
    )

    if status != noErr {
        // Fall back to setting both channels
        address.mSelector = kAudioDevicePropertyVolumeScalar
        for channel: UInt32 in [1, 2] {
            address.mElement = channel
            AudioObjectSetPropertyData(
                deviceID, &address, 0, nil,
                UInt32(MemoryLayout<Float32>.size), &vol
            )
        }
    }
}

func getMute() -> Bool? {
    guard let deviceID = getDefaultOutputDevice() else { return nil }

    var mute: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyMute,
        mScope: kAudioDevicePropertyScopeOutput,
        mElement: kAudioObjectPropertyElementMain
    )

    var status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &mute)

    if status != noErr {
        // Try channel 1
        address.mElement = 1
        status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &mute)
    }

    return status == noErr ? (mute != 0) : nil
}

func setMute(_ mute: Bool) {
    guard let deviceID = getDefaultOutputDevice() else { return }

    var muteVal: UInt32 = mute ? 1 : 0
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyMute,
        mScope: kAudioDevicePropertyScopeOutput,
        mElement: kAudioObjectPropertyElementMain
    )

    var status = AudioObjectSetPropertyData(
        deviceID, &address, 0, nil,
        UInt32(MemoryLayout<UInt32>.size), &muteVal
    )

    if status != noErr {
        // Try setting on channels
        for channel: UInt32 in [1, 2] {
            address.mElement = channel
            AudioObjectSetPropertyData(
                deviceID, &address, 0, nil,
                UInt32(MemoryLayout<UInt32>.size), &muteVal
            )
        }
    }
}

func getOutputDeviceName() -> String? {
    guard let deviceID = getDefaultOutputDevice() else { return nil }

    var name: CFString = "" as CFString
    var size = UInt32(MemoryLayout<CFString>.size)
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceNameCFString,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )

    let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &name)
    return status == noErr ? (name as String) : nil
}

func emitVolume() {
    if let vol = getVolume() {
        print("{ \"event\": \"Volume\", \"arg\": \(Int(vol * 100)) }")
    }
}

func emitMute() {
    if let mute = getMute() {
        print("{ \"event\": \"Mute\", \"arg\": \(mute ? "true" : "false") }")
    }
}

func emitOutputDevices() {
    if let name = getOutputDeviceName() {
        print("{ \"event\": \"OutputDevice\", \"arg\": \"\(escapeJSON(name))\" }")
    }
}

// MARK: - CPU Monitoring

struct CPUInfo {
    var user: UInt64 = 0
    var system: UInt64 = 0
    var idle: UInt64 = 0
    var nice: UInt64 = 0
}

var lastCPU = CPUInfo()

func getCPUUsage() -> Double {
    var cpuInfo: processor_info_array_t?
    var numCpuInfo: mach_msg_type_number_t = 0
    var numCPUs: natural_t = 0

    let err = host_processor_info(
        mach_host_self(),
        PROCESSOR_CPU_LOAD_INFO,
        &numCPUs,
        &cpuInfo,
        &numCpuInfo
    )

    guard err == KERN_SUCCESS, let info = cpuInfo else {
        return 0
    }

    var totalUser: UInt64 = 0
    var totalSystem: UInt64 = 0
    var totalIdle: UInt64 = 0
    var totalNice: UInt64 = 0

    for i in 0..<Int(numCPUs) {
        let offset = Int(CPU_STATE_MAX) * i
        totalUser += UInt64(info[offset + Int(CPU_STATE_USER)])
        totalSystem += UInt64(info[offset + Int(CPU_STATE_SYSTEM)])
        totalIdle += UInt64(info[offset + Int(CPU_STATE_IDLE)])
        totalNice += UInt64(info[offset + Int(CPU_STATE_NICE)])
    }

    // Deallocate the memory allocated by host_processor_info
    let infoSize = vm_size_t(Int(numCpuInfo) * MemoryLayout<integer_t>.size)
    vm_deallocate(mach_task_self_, vm_address_t(bitPattern: info), infoSize)

    let userDiff = totalUser - lastCPU.user
    let systemDiff = totalSystem - lastCPU.system
    let idleDiff = totalIdle - lastCPU.idle
    let niceDiff = totalNice - lastCPU.nice

    lastCPU = CPUInfo(user: totalUser, system: totalSystem, idle: totalIdle, nice: totalNice)

    let total = userDiff + systemDiff + idleDiff + niceDiff
    if total == 0 { return 0 }

    let used = userDiff + systemDiff + niceDiff
    return Double(used) / Double(total) * 100.0
}

func emitCPU() {
    let usage = getCPUUsage()
    print(String(format: "{ \"event\": \"CPU\", \"arg\": %.1f }", usage))
}

// MARK: - Hardware Info

func sysctlString(_ name: String) -> String? {
    var size: Int = 0
    sysctlbyname(name, nil, &size, nil, 0)
    guard size > 0 else { return nil }

    var buffer = [CChar](repeating: 0, count: size)
    sysctlbyname(name, &buffer, &size, nil, 0)
    return String(cString: buffer)
}

func sysctlInt64(_ name: String) -> Int64? {
    var value: Int64 = 0
    var size = MemoryLayout<Int64>.size
    let result = sysctlbyname(name, &value, &size, nil, 0)
    return result == 0 ? value : nil
}

func sysctlInt32(_ name: String) -> Int32? {
    var value: Int32 = 0
    var size = MemoryLayout<Int32>.size
    let result = sysctlbyname(name, &value, &size, nil, 0)
    return result == 0 ? value : nil
}

func emitHardwareInfo() {
    if let model = sysctlString("hw.model") {
        print("{ \"event\": \"Model\", \"arg\": \"\(escapeJSON(model))\" }")
    }

    // CPU name - different on Intel vs Apple Silicon
    if let cpuBrand = sysctlString("machdep.cpu.brand_string") {
        // Intel
        let cleaned = cpuBrand.replacingOccurrences(of: "(R)", with: "")
            .replacingOccurrences(of: "(TM)", with: "")
            .trimmingCharacters(in: .whitespaces)
        print("{ \"event\": \"CPUName\", \"arg\": \"\(escapeJSON(cleaned))\" }")
    } else {
        // Apple Silicon - construct from model
        #if arch(arm64)
        print("{ \"event\": \"CPUName\", \"arg\": \"Apple Silicon\" }")
        #else
        print("{ \"event\": \"CPUName\", \"arg\": \"Unknown\" }")
        #endif
    }

    if let cores = sysctlInt32("hw.physicalcpu") {
        print("{ \"event\": \"Cores\", \"arg\": \"\(cores)\" }")
    }

    if let logical = sysctlInt32("hw.logicalcpu") {
        print("{ \"event\": \"LogicalProcessors\", \"arg\": \"\(logical)\" }")
    }

    if let mem = sysctlInt64("hw.memsize") {
        let gb = Double(mem) / 1024 / 1024 / 1024
        print(String(format: "{ \"event\": \"PhysicalMemory\", \"arg\": \"%.1f GB\" }", gb))
    }

    #if arch(arm64)
    print("{ \"event\": \"Architecture\", \"arg\": \"arm64\" }")
    #else
    print("{ \"event\": \"Architecture\", \"arg\": \"x86_64\" }")
    #endif
}

// MARK: - Network Interface MAC Addresses

func getMACAddresses() -> [(interface: String, mac: String)] {
    var results: [(interface: String, mac: String)] = []

    guard let interfaces = SCNetworkInterfaceCopyAll() as? [SCNetworkInterface] else {
        return results
    }

    for interface in interfaces {
        guard let bsdName = SCNetworkInterfaceGetBSDName(interface) as String?,
              let macAddress = SCNetworkInterfaceGetHardwareAddressString(interface) as String? else {
            continue
        }
        results.append((interface: bsdName, mac: macAddress))
    }

    return results
}

func emitMACAddresses() {
    let macs = getMACAddresses()

    // Emit primary MAC (en0 if available, otherwise first one)
    let primary = macs.first { $0.interface == "en0" } ?? macs.first
    if let primary = primary {
        print("{ \"event\": \"MACAddress\", \"arg\": \"\(escapeJSON(primary.mac))\" }")
    }

    // Emit all MAC addresses as array
    if !macs.isEmpty {
        let jsonArray = macs.map { "{ \"interface\": \"\(escapeJSON($0.interface))\", \"mac\": \"\(escapeJSON($0.mac))\" }" }
        print("{ \"event\": \"MACAddresses\", \"arg\": [\(jsonArray.joined(separator: ", "))] }")
    }
}

// MARK: - Screenshots

// Use screencapture command for macOS 15+ compatibility
func takeScreenshots() {
    let tempDir = FileManager.default.temporaryDirectory

    // Get display count
    var displayCount: UInt32 = 0
    CGGetActiveDisplayList(0, nil, &displayCount)

    guard displayCount > 0 else {
        print("// No displays found")
        return
    }

    // screencapture -D uses display numbers 1, 2, 3... not CGDirectDisplayID
    for displayNum in 1...Int(displayCount) {
        let tempFile = tempDir.appendingPathComponent("nodel_screenshot_\(displayNum).jpg")

        // Use screencapture command - works on all macOS versions including Sequoia
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        process.arguments = [
            "-x",           // no sound
            "-t", "jpg",    // JPEG format
            "-D", String(displayNum), // display number (1, 2, 3...)
            tempFile.path
        ]

        do {
            try process.run()
            process.waitUntilExit()

            guard process.terminationStatus == 0 else {
                // screencapture may fail due to permission
                print("{ \"event\": \"PermissionNeeded\", \"arg\": \"screen-recording\" }")
                continue
            }

            // Read and scale the image
            guard let imageData = try? Data(contentsOf: tempFile),
                  let image = NSImage(data: imageData) else {
                continue
            }

            // Scale to thumbnail
            let maxSize: CGFloat = 400
            let originalSize = image.size
            let scale = min(maxSize / originalSize.width, maxSize / originalSize.height, 1.0)
            let newSize = NSSize(width: originalSize.width * scale, height: originalSize.height * scale)

            let scaledImage = NSImage(size: newSize)
            scaledImage.lockFocus()
            image.draw(in: NSRect(origin: .zero, size: newSize),
                      from: NSRect(origin: .zero, size: originalSize),
                      operation: .copy,
                      fraction: 1.0)
            scaledImage.unlockFocus()

            // Convert to JPEG
            guard let tiffData = scaledImage.tiffRepresentation,
                  let bitmap = NSBitmapImageRep(data: tiffData),
                  let jpegData = bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.1]) else {
                continue
            }

            let base64 = jpegData.base64EncodedString()
            print("{ \"event\": \"Screenshot\(displayNum)\", \"arg\": \"data:image/jpeg;base64,\(base64)\" }")

            // Clean up temp file
            try? FileManager.default.removeItem(at: tempFile)

        } catch {
            print("// Screenshot error: \(error)")
        }
    }
}

// MARK: - Polling Loops

var lastEmittedPeakDB: Float = -60.0

func startAudioMeterPolling() {
    guard !audioMeterPollingActive else { return }
    audioMeterPollingActive = true
    DispatchQueue.global().async {
        // Wait for audio stream to initialize
        Thread.sleep(forTimeInterval: 2)

        while running && audioMeterActive && audioMeterPollingActive {
            Thread.sleep(forTimeInterval: 0.5) // 2 updates per second
            if !running || !audioMeterActive || !audioMeterPollingActive { break }

            // Apply decay if no recent audio samples (silence detection)
            let timeSinceLastSample = Date().timeIntervalSince(lastAudioSampleTime)
            if timeSinceLastSample > 0.3 {
                // Decay toward silence
                currentPeakDB = max(-60, currentPeakDB - 5)
            }

            // Only emit if changed significantly (reduce noise)
            if abs(currentPeakDB - lastEmittedPeakDB) > 0.5 {
                lastEmittedPeakDB = currentPeakDB
                print(String(format: "{ \"event\": \"AudioMeter\", \"arg\": %.1f }", currentPeakDB))
                fflush(stdout)
            }
        }
        audioMeterPollingActive = false
    }
}

// MARK: - Utilities

func escapeJSON(_ str: String) -> String {
    var result = ""
    // First normalize smart quotes and other problematic Unicode chars
    let normalized = str
        .replacingOccurrences(of: "\u{2019}", with: "'")  // Right single quote '
        .replacingOccurrences(of: "\u{2018}", with: "'")  // Left single quote '
        .replacingOccurrences(of: "\u{201C}", with: "\"") // Left double quote "
        .replacingOccurrences(of: "\u{201D}", with: "\"") // Right double quote "
        .replacingOccurrences(of: "\u{2013}", with: "-")  // En dash –
        .replacingOccurrences(of: "\u{2014}", with: "-")  // Em dash —

    for char in normalized {
        switch char {
        case "\"": result += "\\\""
        case "\\": result += "\\\\"
        case "\n": result += "\\n"
        case "\r": result += "\\r"
        case "\t": result += "\\t"
        default: result.append(char)
        }
    }
    return result
}

// MARK: - Entry Point

func shutdown() {
    running = false
    stopAudioMetering()
    CFRunLoopStop(CFRunLoopGetMain())
}

signal(SIGINT) { _ in
    shutdown()
}

signal(SIGTERM) { _ in
    shutdown()
}

main()
