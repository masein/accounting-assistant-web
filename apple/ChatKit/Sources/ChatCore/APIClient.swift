import Foundation

/// An error the server (or the network) gave, said the way the app shows it.
public struct APIError: Error, LocalizedError, Equatable, Sendable {
    public let status: Int
    public let detail: String
    public let code: String?

    public var errorDescription: String? { detail }

    /// The session is over (refresh refused, signed out elsewhere): back to the sign-in screen.
    public var endsSession: Bool { status == 401 }
}

/// The mobile API (/api/mobile/v1): the same sign-in, threads and chat as the Android app. Access tokens last
/// minutes; on a 401 the client trades its refresh token for a new pair once, then retries the request.
public actor APIClient {
    public nonisolated let baseURL: URL
    private let session: URLSession
    private let tokens: TokenStore
    private let device: DeviceInfo
    private var refreshing: Task<Void, Error>?

    public static let apiPath = "api/mobile/v1"

    public init(baseURL: URL, tokens: TokenStore, device: DeviceInfo = .current, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.tokens = tokens
        self.device = device
        self.session = session
    }

    // MARK: - Signing in

    public func signIn(username: String, password: String) async throws -> SignInResult {
        var body = device.payload
        body["username"] = username
        body["password"] = password
        let data = try await send("POST", "auth/login", json: body, authorized: false)
        if let challenge = try? JSONDecoder.api.decode(TwoFactorChallenge.self, from: data), challenge.twoFactorRequired {
            return .twoFactor(challenge: challenge.challenge)
        }
        let signIn = try JSONDecoder.api.decode(SignIn.self, from: data)
        tokens.save(Tokens(access: signIn.accessToken, refresh: signIn.refreshToken))
        return .signedIn(signIn)
    }

    public func confirmTwoFactor(challenge: String, code: String) async throws -> SignIn {
        var body = device.payload
        body["challenge"] = challenge
        body["code"] = code
        let data = try await send("POST", "auth/2fa", json: body, authorized: false)
        let signIn = try JSONDecoder.api.decode(SignIn.self, from: data)
        tokens.save(Tokens(access: signIn.accessToken, refresh: signIn.refreshToken))
        return signIn
    }

    /// Ends this device's session on the server too (best effort), and forgets the tokens.
    public func signOut() async {
        _ = try? await send("DELETE", "session", json: nil)
        tokens.clear()
    }

    public nonisolated var isSignedIn: Bool { tokens.load() != nil }

    public func me() async throws -> Me {
        try await decode(Me.self, "GET", "me")
    }

    // MARK: - Chat

    public func threads() async throws -> [ChatThread] {
        try await decode([ChatThread].self, "GET", "threads")
    }

    public func messages(thread: String) async throws -> [ChatMessage] {
        try await decode([ChatMessage].self, "GET", "threads/\(thread)/messages")
    }

    /// One message to the assistant. `clientMessageID` makes a retry safe: the server answers a repeat once.
    public func send(message: String, thread: String?, clientMessageID: String) async throws -> ChatReply {
        var body: [String: Any] = ["message": message, "client_message_id": clientMessageID]
        if let thread { body["thread_id"] = thread }
        return try await decode(ChatReply.self, "POST", "chat", json: body)
    }

    public func confirm(proposal token: String) async throws -> ProposalOutcome {
        try await decode(ProposalOutcome.self, "POST", "proposals/\(token)/confirm", json: [:])
    }

    public func cancel(proposal token: String) async throws -> ProposalOutcome {
        try await decode(ProposalOutcome.self, "POST", "proposals/\(token)/cancel", json: [:])
    }

    /// Takes a confirmed entry back, within its undo window (410 after it).
    public func undo(posting auditLogID: String) async throws -> ProposalOutcome {
        try await decode(ProposalOutcome.self, "POST", "postings/\(auditLogID)/undo", json: [:])
    }

    // MARK: - Requests

    private func decode<T: Decodable>(_ type: T.Type, _ method: String, _ path: String,
                                      json: [String: Any]? = nil) async throws -> T {
        let data = try await send(method, path, json: json)
        do {
            return try JSONDecoder.api.decode(T.self, from: data)
        } catch {
            throw APIError(status: 0, detail: "The server's answer wasn't understood.", code: "decode")
        }
    }

    private func send(_ method: String, _ path: String, json: [String: Any]?, authorized: Bool = true,
                      retried: Bool = false) async throws -> Data {
        var request = URLRequest(url: baseURL.appendingPathComponent(Self.apiPath).appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = path == "chat" ? 180 : 30            // an assistant turn can take a while
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(device.appVersion, forHTTPHeaderField: "X-App-Version")
        if let language = device.language { request.setValue(language, forHTTPHeaderField: "Accept-Language") }
        if let json {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
        }
        if authorized, let access = tokens.load()?.access {
            request.setValue("Bearer \(access)", forHTTPHeaderField: "Authorization")
        }
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError(status: -1, detail: "Can't reach the server. Check the connection and try again.",
                           code: "offline")
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 401 && authorized && !retried && tokens.load() != nil {
            try await refreshTokens()
            return try await send(method, path, json: json, authorized: authorized, retried: true)
        }
        guard (200..<300).contains(status) else {
            let detail = (try? JSONDecoder.api.decode(APIErrorBody.self, from: data))?.detail
            let code = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "X-Error-Code")
            if status == 401 && authorized { tokens.clear() }
            throw APIError(status: status, detail: detail ?? "The server answered \(status).", code: code)
        }
        return data
    }

    /// One refresh at a time: requests that all got a 401 wait for the same new pair.
    private func refreshTokens() async throws {
        if let refreshing { return try await refreshing.value }
        let task = Task { try await self.exchangeRefreshToken() }
        refreshing = task
        defer { refreshing = nil }
        do {
            try await task.value
        } catch {
            tokens.clear()
            throw (error as? APIError) ?? APIError(status: 401, detail: "Sign in again.", code: "session_ended")
        }
    }

    private func exchangeRefreshToken() async throws {
        guard let refresh = tokens.load()?.refresh else {
            throw APIError(status: 401, detail: "Sign in again.", code: "session_ended")
        }
        let data = try await send("POST", "auth/refresh", json: ["refresh_token": refresh, "app_version": device.appVersion],
                                  authorized: false, retried: true)
        let signIn = try JSONDecoder.api.decode(SignIn.self, from: data)
        tokens.save(Tokens(access: signIn.accessToken, refresh: signIn.refreshToken))
    }
}

struct TwoFactorChallenge: Decodable {
    let twoFactorRequired: Bool
    let challenge: String
}

public struct Me: Decodable, Equatable, Sendable {
    public let user: User
    public let company: Company?
}

/// What the server is told about this device at sign-in (it lists it under the user's devices).
public struct DeviceInfo: Sendable {
    public var name: String
    public var platform: String
    public var appVersion: String
    public var language: String?

    var payload: [String: Any] { ["device_name": name, "platform": platform, "app_version": appVersion] }

    public static var current: DeviceInfo {
        let version = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "1.0.0"
        #if os(iOS)
        let platform = "ios"
        let name = "iPhone"
        #else
        let platform = "macos"
        let name = Host.current().localizedName ?? "Mac"
        #endif
        return DeviceInfo(name: String(name.prefix(80)), platform: platform, appVersion: version,
                          language: Locale.preferredLanguages.first)
    }
}

extension JSONDecoder {
    static let api: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
}
