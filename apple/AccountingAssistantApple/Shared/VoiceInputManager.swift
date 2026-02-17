import Foundation

#if canImport(Speech) && canImport(AVFoundation)
import AVFoundation
import Speech

@MainActor
final class VoiceInputManager: ObservableObject {
    @Published var transcript: String = ""
    @Published var isRecording: Bool = false
    @Published var lastError: String?

    private let audioEngine = AVAudioEngine()
    private let speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?

    func requestPermissions() async -> Bool {
        let speechAuthorized = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }

        #if os(iOS)
        let micAuthorized = await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
        #elseif os(macOS)
        let micAuthorized = await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                continuation.resume(returning: granted)
            }
        }
        #else
        let micAuthorized = true
        #endif

        return speechAuthorized && micAuthorized
    }

    func start() async {
        guard !isRecording else { return }
        lastError = nil

        let allowed = await requestPermissions()
        guard allowed else {
            lastError = "Speech permission denied."
            return
        }

        do {
            try configureAudioSessionIfNeeded()
            recognitionTask?.cancel()
            recognitionTask = nil

            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            recognitionRequest = request

            let inputNode = audioEngine.inputNode
            let format = inputNode.outputFormat(forBus: 0)
            inputNode.removeTap(onBus: 0)
            inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
                self?.recognitionRequest?.append(buffer)
            }

            audioEngine.prepare()
            try audioEngine.start()

            recognitionTask = speechRecognizer?.recognitionTask(with: request) { [weak self] result, error in
                guard let self else { return }
                Task { @MainActor in
                    if let result {
                        self.transcript = result.bestTranscription.formattedString
                    }
                    if let error {
                        self.lastError = error.localizedDescription
                        self.stop()
                    }
                }
            }
            isRecording = true
        } catch {
            lastError = error.localizedDescription
            stop()
        }
    }

    func stop() {
        guard isRecording else { return }
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
        isRecording = false
    }

    private func configureAudioSessionIfNeeded() throws {
        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: [.duckOthers])
        try session.setActive(true, options: .notifyOthersOnDeactivation)
        #endif
    }
}

#else

@MainActor
final class VoiceInputManager: ObservableObject {
    @Published var transcript: String = ""
    @Published var isRecording: Bool = false
    @Published var lastError: String?

    func requestPermissions() async -> Bool { false }
    func start() async { lastError = "Live voice capture is not available on this platform." }
    func stop() { isRecording = false }
}

#endif
