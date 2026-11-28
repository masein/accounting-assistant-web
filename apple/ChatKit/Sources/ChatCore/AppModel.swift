import Foundation
import Combine

/// Signing in and out, and which server. The chat itself is `ChatModel`.
@MainActor
public final class AppModel: ObservableObject {
    /// `updateRequired`: the server refused this version of the app (426): the update screen, until it's updated.
    public enum Phase: Equatable { case starting, signedOut, twoFactor(challenge: String), signedIn, updateRequired }

    @Published public private(set) var phase: Phase = .starting
    @Published public private(set) var user: User?
    @Published public private(set) var company: Company?
    @Published public private(set) var busy = false
    @Published public var error: String?
    @Published public var serverAddress: String
    @Published public private(set) var chat: ChatModel?

    public static let defaultServer = "https://accounting.netixsystem.com"
    private let defaults: UserDefaults
    private let makeTokens: (URL) -> TokenStore
    private let session: URLSession
    private var api: APIClient?

    public var l10n: L10n { L10n(user?.preferredLanguage ?? Locale.preferredLanguages.first) }

    public init(defaults: UserDefaults = .standard, session: URLSession = .shared,
                tokens: ((URL) -> TokenStore)? = nil) {
        self.defaults = defaults
        self.session = session
        self.makeTokens = tokens ?? { url in KeychainTokenStore(account: url.host ?? url.absoluteString) }
        self.serverAddress = defaults.string(forKey: "server") ?? Self.defaultServer
    }

    /// At launch: a saved session goes straight to the chat (checked with /me), else the sign-in screen.
    public func restore() async {
        guard let url = serverURL(serverAddress) else { phase = .signedOut; return }
        let api = makeClient(url)
        guard api.isSignedIn else { phase = .signedOut; return }
        do {
            let me = try await api.me()
            open(api, user: me.user, company: me.company)
        } catch let e as APIError where e.updateRequired {
            phase = .updateRequired
        } catch let e as APIError where e.endsSession {
            phase = .signedOut
        } catch {
            // offline at launch: the saved session still stands; the chat says when the server answers
            open(api, user: nil, company: nil)
        }
    }

    public func signIn(username: String, password: String) async {
        guard let url = serverURL(serverAddress) else {
            error = "That isn't a server address — for example \(Self.defaultServer)"
            return
        }
        busy = true; error = nil
        defer { busy = false }
        let api = makeClient(url)
        do {
            switch try await api.signIn(username: username.trimmingCharacters(in: .whitespaces), password: password) {
            case .signedIn(let s):
                defaults.set(serverAddress, forKey: "server")
                open(api, user: s.user, company: s.company)
            case .twoFactor(let challenge):
                self.api = api
                phase = .twoFactor(challenge: challenge)
            }
        } catch let e as APIError where e.updateRequired {
            phase = .updateRequired
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// From the update screen: ask the server again (after an update, or a server that lowered its minimum).
    public func checkAgain() async {
        phase = .starting
        await restore()
    }

    public func confirmTwoFactor(code: String) async {
        guard case .twoFactor(let challenge) = phase, let api else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            let s = try await api.confirmTwoFactor(challenge: challenge, code: code.trimmingCharacters(in: .whitespaces))
            defaults.set(serverAddress, forKey: "server")
            open(api, user: s.user, company: s.company)
        } catch let e as APIError where e.updateRequired {
            phase = .updateRequired
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// A scripted run (the app's debug Automation) starts signed out of `server`, whatever the Keychain kept.
    public func startFresh(server: String) {
        serverAddress = server
        if let url = serverURL(server) { makeTokens(url).clear() }
        api = nil; chat = nil; user = nil; company = nil
        phase = .signedOut
    }

    public func backToSignIn() {
        phase = .signedOut
        error = nil
    }

    public func signOut() async {
        await api?.signOut()
        api = nil; chat = nil; user = nil; company = nil
        phase = .signedOut
    }

    private func open(_ api: APIClient, user: User?, company: Company?) {
        self.api = api
        self.user = user
        self.company = company
        let chat = ChatModel(api: api)
        chat.onSessionEnded = { [weak self] in
            Task { await self?.signOut() }
        }
        chat.onUpdateRequired = { [weak self] in
            self?.phase = .updateRequired
        }
        self.chat = chat
        phase = .signedIn
    }

    private func makeClient(_ url: URL) -> APIClient {
        APIClient(baseURL: url, tokens: makeTokens(url), session: session)
    }

    /// "accounting.example.com" → https://accounting.example.com; http only for this machine (testing).
    public func serverURL(_ text: String) -> URL? {
        var t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        while t.hasSuffix("/") { t.removeLast() }
        if !t.contains("://") { t = "https://" + t }
        guard let url = URL(string: t), let host = url.host, !host.isEmpty else { return nil }
        if url.scheme == "https" { return url }
        if url.scheme == "http" && (host == "localhost" || host == "127.0.0.1") { return url }
        return nil
    }
}
