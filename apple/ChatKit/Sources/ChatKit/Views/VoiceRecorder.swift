import AVFoundation
import Foundation

/// Hold to talk: AAC in an .m4a, mono, small enough to send at once (as the Android app records it). Let go and the
/// note goes to the server for its words (`ChatModel.heard`); let go within a moment and there is nothing to send.
@MainActor
final class VoiceRecorder: ObservableObject {
    @Published private(set) var recording = false
    /// the button is held (the recording may still be starting: the microphone's permission is asked first)
    private(set) var pressing = false
    private var recorder: AVAudioRecorder?
    private var file: URL?
    private var startedAt = Date()

    /// shorter than this holds no words
    static let minimum: TimeInterval = 0.6

    enum Outcome { case heard(Data), tooShort, nothing }

    func press(onDenied: @escaping () -> Void) {
        pressing = true
        Task {
            guard await Self.permission() else {
                pressing = false
                onDenied()
                return
            }
            guard pressing else { return }                     // let go while the permission was asked
            begin()
        }
    }

    func release() -> Outcome {
        let held = pressing
        pressing = false
        guard let recorder, let file else { return held ? .tooShort : .nothing }
        let length = Date().timeIntervalSince(startedAt)
        recorder.stop()
        self.recorder = nil
        self.file = nil
        recording = false
        #if os(iOS)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
        defer { try? FileManager.default.removeItem(at: file) }
        guard length >= Self.minimum, let data = try? Data(contentsOf: file), !data.isEmpty else { return .tooShort }
        return .heard(data)
    }

    private func begin() {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("voice-\(UUID().uuidString).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
        ]
        do {
            #if os(iOS)
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
            try session.setActive(true)
            #endif
            let r = try AVAudioRecorder(url: url, settings: settings)
            guard r.record() else { pressing = false; return }
            recorder = r
            file = url
            startedAt = Date()
            recording = true
        } catch {
            pressing = false
        }
    }

    private static func permission() async -> Bool {
        #if os(iOS)
        return await AVAudioApplication.requestRecordPermission()
        #else
        return await AVCaptureDevice.requestAccess(for: .audio)
        #endif
    }
}
