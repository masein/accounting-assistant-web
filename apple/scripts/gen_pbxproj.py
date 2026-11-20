"""Writes apple/AccountingChat.xcodeproj/project.pbxproj: one multiplatform app target (iPhone, iPad, Mac) whose
sources are the package's AccountingChatApp folder (the same files apple/build-mac.sh builds) and whose resources
are the AccountingChat folder (icon, accent colour, Info.plist), linking the local ChatKit package."""
import sys

P = sys.argv[1]
I = {k: f"AC{n:022X}" for n, k in enumerate([
    "buildfile", "app", "group_src", "frameworks", "main", "products", "project", "target", "sources", "resources",
    "pkgdep", "pkgref", "exceptions", "cfglist_proj", "cfglist_target", "proj_debug", "proj_release", "tgt_debug",
    "tgt_release", "group_app"], start=1)}

common_proj = """				ALWAYS_SEARCH_USER_PATHS = NO;
				ASSETCATALOG_COMPILER_GENERATE_SWIFT_ASSET_SYMBOL_EXTENSIONS = YES;
				CLANG_ENABLE_MODULES = YES;
				CLANG_ENABLE_OBJC_ARC = YES;
				COPY_PHASE_STRIP = NO;
				ENABLE_STRICT_OBJC_MSGSEND = YES;
				ENABLE_USER_SCRIPT_SANDBOXING = YES;
				GCC_NO_COMMON_BLOCKS = YES;
				IPHONEOS_DEPLOYMENT_TARGET = 17.0;
				MACOSX_DEPLOYMENT_TARGET = 14.0;
				SWIFT_EMIT_LOC_STRINGS = YES;"""
proj_debug = common_proj + """
				DEBUG_INFORMATION_FORMAT = dwarf;
				ENABLE_TESTABILITY = YES;
				GCC_OPTIMIZATION_LEVEL = 0;
				ONLY_ACTIVE_ARCH = YES;
				SWIFT_ACTIVE_COMPILATION_CONDITIONS = "DEBUG $(inherited)";
				SWIFT_OPTIMIZATION_LEVEL = "-Onone";"""
proj_release = common_proj + """
				DEBUG_INFORMATION_FORMAT = "dwarf-with-dsym";
				ENABLE_NS_ASSERTIONS = NO;
				SWIFT_COMPILATION_MODE = wholemodule;"""
target = """				ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
				ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME = AccentColor;
				CODE_SIGN_STYLE = Automatic;
				"CODE_SIGN_IDENTITY[sdk=macosx*]" = "-";
				CURRENT_PROJECT_VERSION = 1;
				ENABLE_APP_SANDBOX = YES;
				ENABLE_HARDENED_RUNTIME = YES;
				ENABLE_OUTGOING_NETWORK_CONNECTIONS = YES;
				ENABLE_PREVIEWS = YES;
				GENERATE_INFOPLIST_FILE = YES;
				INFOPLIST_FILE = AccountingChat/Info.plist;
				INFOPLIST_KEY_CFBundleDisplayName = "Accounting Chat";
				INFOPLIST_KEY_LSApplicationCategoryType = "public.app-category.finance";
				"INFOPLIST_KEY_UIApplicationSceneManifest_Generation[sdk=iphone*]" = YES;
				"INFOPLIST_KEY_UILaunchScreen_Generation[sdk=iphone*]" = YES;
				"INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad[sdk=iphone*]" = "UIInterfaceOrientationPortrait UIInterfaceOrientationPortraitUpsideDown UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight";
				"INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone[sdk=iphone*]" = "UIInterfaceOrientationPortrait UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight";
				"LD_RUNPATH_SEARCH_PATHS[sdk=iphone*]" = "$(inherited) @executable_path/Frameworks";
				"LD_RUNPATH_SEARCH_PATHS[sdk=macosx*]" = "$(inherited) @executable_path/../Frameworks";
				MARKETING_VERSION = 1.0.0;
				PRODUCT_BUNDLE_IDENTIFIER = app.accountingassistant.chat;
				PRODUCT_NAME = "$(TARGET_NAME)";
				SDKROOT = auto;
				SUPPORTED_PLATFORMS = "iphoneos iphonesimulator macosx";
				SUPPORTS_MACCATALYST = NO;
				SWIFT_VERSION = 5.0;
				TARGETED_DEVICE_FAMILY = "1,2";"""


def config(oid, name, settings):
    return f"""		{oid} /* {name} */ = {{
			isa = XCBuildConfiguration;
			buildSettings = {{
{settings}
			}};
			name = {name};
		}};"""


pbx = f"""// !$*UTF8*$!
{{
	archiveVersion = 1;
	classes = {{
	}};
	objectVersion = 77;
	objects = {{

/* Begin PBXBuildFile section */
		{I['buildfile']} /* ChatKit in Frameworks */ = {{isa = PBXBuildFile; productRef = {I['pkgdep']} /* ChatKit */; }};
/* End PBXBuildFile section */

/* Begin PBXFileReference section */
		{I['app']} /* AccountingChat.app */ = {{isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = AccountingChat.app; sourceTree = BUILT_PRODUCTS_DIR; }};
/* End PBXFileReference section */

/* Begin PBXFileSystemSynchronizedBuildFileExceptionSet section */
		{I['exceptions']} /* Exceptions for "AccountingChat" folder in "AccountingChat" target */ = {{
			isa = PBXFileSystemSynchronizedBuildFileExceptionSet;
			membershipExceptions = (
				Info.plist,
			);
			target = {I['target']} /* AccountingChat */;
		}};
/* End PBXFileSystemSynchronizedBuildFileExceptionSet section */

/* Begin PBXFileSystemSynchronizedRootGroup section */
		{I['group_app']} /* ChatKit/Sources/AccountingChatApp */ = {{
			isa = PBXFileSystemSynchronizedRootGroup;
			path = ChatKit/Sources/AccountingChatApp;
			sourceTree = "<group>";
		}};
		{I['group_src']} /* AccountingChat */ = {{
			isa = PBXFileSystemSynchronizedRootGroup;
			exceptions = (
				{I['exceptions']} /* Exceptions for "AccountingChat" folder in "AccountingChat" target */,
			);
			path = AccountingChat;
			sourceTree = "<group>";
		}};
/* End PBXFileSystemSynchronizedRootGroup section */

/* Begin PBXFrameworksBuildPhase section */
		{I['frameworks']} /* Frameworks */ = {{
			isa = PBXFrameworksBuildPhase;
			buildActionMask = 2147483647;
			files = (
				{I['buildfile']} /* ChatKit in Frameworks */,
			);
			runOnlyForDeploymentPostprocessing = 0;
		}};
/* End PBXFrameworksBuildPhase section */

/* Begin PBXGroup section */
		{I['main']} = {{
			isa = PBXGroup;
			children = (
				{I['group_app']} /* ChatKit/Sources/AccountingChatApp */,
				{I['group_src']} /* AccountingChat */,
				{I['products']} /* Products */,
			);
			sourceTree = "<group>";
		}};
		{I['products']} /* Products */ = {{
			isa = PBXGroup;
			children = (
				{I['app']} /* AccountingChat.app */,
			);
			name = Products;
			sourceTree = "<group>";
		}};
/* End PBXGroup section */

/* Begin PBXNativeTarget section */
		{I['target']} /* AccountingChat */ = {{
			isa = PBXNativeTarget;
			buildConfigurationList = {I['cfglist_target']} /* Build configuration list for PBXNativeTarget "AccountingChat" */;
			buildPhases = (
				{I['sources']} /* Sources */,
				{I['frameworks']} /* Frameworks */,
				{I['resources']} /* Resources */,
			);
			buildRules = (
			);
			dependencies = (
			);
			fileSystemSynchronizedGroups = (
				{I['group_app']} /* ChatKit/Sources/AccountingChatApp */,
				{I['group_src']} /* AccountingChat */,
			);
			name = AccountingChat;
			packageProductDependencies = (
				{I['pkgdep']} /* ChatKit */,
			);
			productName = AccountingChat;
			productReference = {I['app']} /* AccountingChat.app */;
			productType = "com.apple.product-type.application";
		}};
/* End PBXNativeTarget section */

/* Begin PBXProject section */
		{I['project']} /* Project object */ = {{
			isa = PBXProject;
			attributes = {{
				BuildIndependentTargetsInParallel = 1;
				LastSwiftUpdateCheck = 2720;
				LastUpgradeCheck = 2720;
				TargetAttributes = {{
					{I['target']} = {{
						CreatedOnToolsVersion = 27.2;
					}};
				}};
			}};
			buildConfigurationList = {I['cfglist_proj']} /* Build configuration list for PBXProject "AccountingChat" */;
			developmentRegion = en;
			hasScannedForEncodings = 0;
			knownRegions = (
				en,
				Base,
				fa,
				ar,
				es,
			);
			mainGroup = {I['main']};
			minimizedProjectReferenceProxies = 1;
			packageReferences = (
				{I['pkgref']} /* XCLocalSwiftPackageReference "ChatKit" */,
			);
			preferredProjectObjectVersion = 77;
			productRefGroup = {I['products']} /* Products */;
			projectDirPath = "";
			projectRoot = "";
			targets = (
				{I['target']} /* AccountingChat */,
			);
		}};
/* End PBXProject section */

/* Begin PBXResourcesBuildPhase section */
		{I['resources']} /* Resources */ = {{
			isa = PBXResourcesBuildPhase;
			buildActionMask = 2147483647;
			files = (
			);
			runOnlyForDeploymentPostprocessing = 0;
		}};
/* End PBXResourcesBuildPhase section */

/* Begin PBXSourcesBuildPhase section */
		{I['sources']} /* Sources */ = {{
			isa = PBXSourcesBuildPhase;
			buildActionMask = 2147483647;
			files = (
			);
			runOnlyForDeploymentPostprocessing = 0;
		}};
/* End PBXSourcesBuildPhase section */

/* Begin XCBuildConfiguration section */
{config(I['proj_debug'], 'Debug', proj_debug)}
{config(I['proj_release'], 'Release', proj_release)}
{config(I['tgt_debug'], 'Debug', target)}
{config(I['tgt_release'], 'Release', target)}
/* End XCBuildConfiguration section */

/* Begin XCConfigurationList section */
		{I['cfglist_proj']} /* Build configuration list for PBXProject "AccountingChat" */ = {{
			isa = XCConfigurationList;
			buildConfigurations = (
				{I['proj_debug']} /* Debug */,
				{I['proj_release']} /* Release */,
			);
			defaultConfigurationIsVisible = 0;
			defaultConfigurationName = Release;
		}};
		{I['cfglist_target']} /* Build configuration list for PBXNativeTarget "AccountingChat" */ = {{
			isa = XCConfigurationList;
			buildConfigurations = (
				{I['tgt_debug']} /* Debug */,
				{I['tgt_release']} /* Release */,
			);
			defaultConfigurationIsVisible = 0;
			defaultConfigurationName = Release;
		}};
/* End XCConfigurationList section */

/* Begin XCLocalSwiftPackageReference section */
		{I['pkgref']} /* XCLocalSwiftPackageReference "ChatKit" */ = {{
			isa = XCLocalSwiftPackageReference;
			relativePath = ChatKit;
		}};
/* End XCLocalSwiftPackageReference section */

/* Begin XCSwiftPackageProductDependency section */
		{I['pkgdep']} /* ChatKit */ = {{
			isa = XCSwiftPackageProductDependency;
			productName = ChatKit;
		}};
/* End XCSwiftPackageProductDependency section */
	}};
	rootObject = {I['project']} /* Project object */;
}}
"""
open(P, "w").write(pbx)
print("written", len(pbx))
