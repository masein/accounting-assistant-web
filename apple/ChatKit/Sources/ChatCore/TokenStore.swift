import Foundation
import Security

public struct Tokens: Codable, Equatable, Sendable {
    public let access: String
    public let refresh: String

    public init(access: String, refresh: String) {
        self.access = access
        self.refresh = refresh
    }
}

/// Where the session's tokens live. The app keeps them in the Keychain; tests in memory.
public protocol TokenStore: AnyObject, Sendable {
    func load() -> Tokens?
    func save(_ tokens: Tokens)
    func clear()
}

/// The Keychain (this device only, available after the first unlock): never in UserDefaults or a file.
public final class KeychainTokenStore: TokenStore, @unchecked Sendable {
    private let service: String
    private let account: String
    private let lock = NSLock()

    public init(service: String = "accounting-chat", account: String) {
        self.service = service
        self.account = account
    }

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    public func load() -> Tokens? {
        lock.lock(); defer { lock.unlock() }
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return try? JSONDecoder().decode(Tokens.self, from: data)
    }

    public func save(_ tokens: Tokens) {
        lock.lock(); defer { lock.unlock() }
        guard let data = try? JSONEncoder().encode(tokens) else { return }
        SecItemDelete(query as CFDictionary)
        var q = query
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(q as CFDictionary, nil)
    }

    public func clear() {
        lock.lock(); defer { lock.unlock() }
        SecItemDelete(query as CFDictionary)
    }
}

public final class MemoryTokenStore: TokenStore, @unchecked Sendable {
    private var tokens: Tokens?
    private let lock = NSLock()

    public init(_ tokens: Tokens? = nil) { self.tokens = tokens }

    public func load() -> Tokens? { lock.lock(); defer { lock.unlock() }; return tokens }
    public func save(_ tokens: Tokens) { lock.lock(); defer { lock.unlock() }; self.tokens = tokens }
    public func clear() { lock.lock(); defer { lock.unlock() }; tokens = nil }
}
