import SwiftUI

/// What's typed on the sign-in screen. (An object rather than `@State`: SwiftUI's `@State` is a macro whose plugin
/// ships only with Xcode, and the Mac app also builds with the Command Line Tools alone — apple/build-mac.sh.)
final class LoginFields: ObservableObject {
    @Published var username = ""
    @Published var password = ""
    @Published var code = ""
}

public struct LoginView: View {
    @ObservedObject var app: AppModel
    @StateObject private var fields = LoginFields()

    public init(app: AppModel) { self.app = app }

    private var t: L10n { app.l10n }

    public var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                VStack(spacing: 10) {
                    Image(systemName: "bubble.left.and.text.bubble.right.fill")
                        .font(.system(size: 44))
                        .foregroundStyle(Theme.primary)
                        .accessibilityHidden(true)
                    Text(t[.appName]).font(.title.bold())
                }
                .padding(.top, 40)

                if case .twoFactor = app.phase {
                    twoFactor
                } else {
                    form
                }

                if let error = app.error {
                    Text(error)
                        .font(.callout)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                        .accessibilityIdentifier("login-error")
                }
            }
            .frame(maxWidth: 380)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .background(Theme.canvas)
    }

    private var form: some View {
        VStack(spacing: 14) {
            field(t[.server]) {
                TextField("https://…", text: $app.serverAddress)
                    .textContentType(.URL)
                    #if os(iOS)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    #endif
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("server")
            }
            field(t[.username]) {
                TextField(t[.username], text: $fields.username)
                    .textContentType(.username)
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    #endif
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("username")
            }
            field(t[.password]) {
                SecureField(t[.password], text: $fields.password)
                    .textContentType(.password)
                    .onSubmit(signIn)
                    .accessibilityIdentifier("password")
            }
            Button(action: signIn) {
                Text(app.busy ? t[.signingIn] : t[.signIn])
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.primary)
            .controlSize(.large)
            .disabled(app.busy || fields.username.isEmpty || fields.password.isEmpty)
            .keyboardShortcut(.defaultAction)
            .accessibilityIdentifier("sign-in")
        }
    }

    private var twoFactor: some View {
        VStack(spacing: 14) {
            Text(t[.twoFactorTitle]).font(.headline)
            Text(t[.twoFactorHint]).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
            field(t[.code]) {
                TextField("123456", text: $fields.code)
                    .textContentType(.oneTimeCode)
                    #if os(iOS)
                    .keyboardType(.numberPad)
                    #endif
                    .onSubmit(verify)
                    .accessibilityIdentifier("code")
            }
            Button(action: verify) {
                Text(t[.verify]).frame(maxWidth: .infinity).padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.primary)
            .controlSize(.large)
            .disabled(app.busy || fields.code.isEmpty)
            Button(t[.cancelAction]) { app.backToSignIn() }
                .buttonStyle(.borderless)
        }
    }

    private func field<Content: View>(_ label: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(.footnote.weight(.medium)).foregroundStyle(.secondary)
            content()
                .textFieldStyle(.plain)
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 10).fill(Theme.card))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(.quaternary))
        }
    }

    private func signIn() {
        guard !fields.username.isEmpty, !fields.password.isEmpty, !app.busy else { return }
        Task { await app.signIn(username: fields.username, password: fields.password) }
    }

    private func verify() {
        guard !fields.code.isEmpty, !app.busy else { return }
        Task { await app.confirmTwoFactor(code: fields.code) }
    }
}
