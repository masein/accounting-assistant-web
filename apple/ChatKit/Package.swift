// swift-tools-version:5.10
// The chat app for iPhone, iPad and Mac (apple/README.md):
//   ChatCore          — the mobile API client, the session (Keychain), the chat's state and words; no UI
//   ChatKit           — the screens (SwiftUI), on top of ChatCore
//   AccountingChatApp — the app's entry point (and its debug-only scripted run); the Xcode project builds the same
//                       files for iPhone, iPad and Mac, and apple/build-mac.sh builds the Mac app from this package
//                       with the Command Line Tools alone — no Xcode needed.
// Tests use Swift Testing, which the Command Line Tools have too: `swift test` runs on any Mac.
import PackageDescription

let package = Package(
    name: "ChatKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "ChatKit", targets: ["ChatKit"]),
        .library(name: "ChatCore", targets: ["ChatCore"]),
        .executable(name: "AccountingChat", targets: ["AccountingChatApp"]),
    ],
    targets: [
        .target(name: "ChatCore"),
        .target(name: "ChatKit", dependencies: ["ChatCore"]),
        .executableTarget(name: "AccountingChatApp", dependencies: ["ChatKit"]),
        .testTarget(name: "ChatCoreTests", dependencies: ["ChatCore"], resources: [.copy("Fixtures")]),
    ]
)
