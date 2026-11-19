// swift-tools-version:5.10
// The chat app for iPhone, iPad and Mac (apple/README.md). The app target (apple/AccountingChat.xcodeproj) is only
// the entry point around this package:
//   ChatCore — the mobile API client, the session (Keychain), the chat's state and words; no UI, so it builds and
//              is tested with `swift test` on any Mac, Xcode or not;
//   ChatKit  — the screens (SwiftUI), on top of ChatCore.
import PackageDescription

let package = Package(
    name: "ChatKit",
    defaultLocalization: "en",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "ChatKit", targets: ["ChatKit"]),
        .library(name: "ChatCore", targets: ["ChatCore"]),
    ],
    targets: [
        .target(name: "ChatCore"),
        .target(name: "ChatKit", dependencies: ["ChatCore"]),
        .testTarget(name: "ChatCoreTests", dependencies: ["ChatCore"], resources: [.copy("Fixtures")]),
    ]
)
