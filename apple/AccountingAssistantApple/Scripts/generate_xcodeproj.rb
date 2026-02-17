#!/usr/bin/env ruby
# frozen_string_literal: true

require 'xcodeproj'
require 'fileutils'

root = File.expand_path('..', __dir__)
project_path = File.join(root, 'AccountingAssistantApple.xcodeproj')
FileUtils.rm_rf(project_path) if File.exist?(project_path)

project = Xcodeproj::Project.new(project_path)
project.root_object.attributes['LastSwiftUpdateCheck'] = '1600'
project.root_object.attributes['LastUpgradeCheck'] = '1600'

main_group = project.main_group
shared_group = main_group.new_group('Shared', 'Shared')
ios_group = main_group.new_group('iOS', 'iOS')
mac_group = main_group.new_group('macOS', 'macOS')
watch_group = main_group.new_group('watchOS', 'watchOS')

shared_core_files = %w[
  Shared/AppConfig.swift
  Shared/AssistantModels.swift
  Shared/AssistantAPI.swift
  Shared/VoiceInputManager.swift
  Shared/ChatViewModel.swift
]

shared_platform_ui_files = %w[
  Shared/ChatViews.swift
]

watch_ui_files = %w[
  Shared/WatchChatView.swift
]
ios_files = ['iOS/AccountingAssistantIOSApp.swift']
mac_files = ['macOS/AccountingAssistantMacApp.swift']
watch_files = ['watchOS/AccountingAssistantWatchApp.swift']

ios_target = project.new_target(:application, 'AccountingAssistant iOS', :ios, '17.0')
mac_target = project.new_target(:application, 'AccountingAssistant macOS', :osx, '14.0')
watch_target = project.new_target(:application, 'AccountingAssistant watchOS', :watchos, '10.0')

def add_sources(project, target, files, shared_group, ios_group, mac_group, watch_group)
  files.each do |path|
    group =
      if path.start_with?('Shared/')
        shared_group
      elsif path.start_with?('iOS/')
        ios_group
      elsif path.start_with?('macOS/')
        mac_group
      else
        watch_group
      end
    file_ref = group.new_file(path.sub(%r{^(Shared|iOS|macOS|watchOS)/}, ''))
    target.add_file_references([file_ref])
  end
end

add_sources(project, ios_target, shared_core_files + shared_platform_ui_files + ios_files, shared_group, ios_group, mac_group, watch_group)
add_sources(project, mac_target, shared_core_files + shared_platform_ui_files + mac_files, shared_group, ios_group, mac_group, watch_group)
add_sources(project, watch_target, shared_core_files + watch_ui_files + watch_files, shared_group, ios_group, mac_group, watch_group)

[project, ios_target, mac_target, watch_target].first # keep rubocop quiet in generated code contexts

def configure_target(target, bundle_id:, display_name:, platform:)
  target.build_configurations.each do |config|
    settings = config.build_settings
    settings['SWIFT_VERSION'] = '5.0'
    settings['GENERATE_INFOPLIST_FILE'] = 'YES'
    settings['CODE_SIGN_STYLE'] = 'Automatic'
    settings['DEVELOPMENT_TEAM'] = ''
    settings['PRODUCT_BUNDLE_IDENTIFIER'] = bundle_id
    settings['PRODUCT_NAME'] = '$(TARGET_NAME)'
    settings['MARKETING_VERSION'] = '1.0'
    settings['CURRENT_PROJECT_VERSION'] = '1'
    settings['INFOPLIST_KEY_CFBundleDisplayName'] = display_name
    settings['INFOPLIST_KEY_NSMicrophoneUsageDescription'] = 'Allow voice commands for accounting chat.'
    settings['INFOPLIST_KEY_NSSpeechRecognitionUsageDescription'] = 'Allow speech-to-text for faster bookkeeping.'
    settings['INFOPLIST_KEY_NSPhotoLibraryUsageDescription'] = 'Allow attaching receipt images to accounting chat.'
    settings['INFOPLIST_KEY_NSAppTransportSecurity_NSAllowsArbitraryLoads'] = 'YES'

    case platform
    when :ios
      settings['IPHONEOS_DEPLOYMENT_TARGET'] = '17.0'
      settings['TARGETED_DEVICE_FAMILY'] = '1,2'
      settings['SUPPORTED_PLATFORMS'] = 'iphoneos iphonesimulator'
    when :mac
      settings['MACOSX_DEPLOYMENT_TARGET'] = '14.0'
      settings['SUPPORTED_PLATFORMS'] = 'macosx'
      settings['CODE_SIGN_IDENTITY[sdk=macosx*]'] = '-'
    when :watch
      settings['WATCHOS_DEPLOYMENT_TARGET'] = '10.0'
      settings['TARGETED_DEVICE_FAMILY'] = '4'
      settings['SUPPORTED_PLATFORMS'] = 'watchos watchsimulator'
    end
  end
end

configure_target(
  ios_target,
  bundle_id: 'com.masein.accountingassistant.ios',
  display_name: 'Accounting Assistant',
  platform: :ios
)
configure_target(
  mac_target,
  bundle_id: 'com.masein.accountingassistant.macos',
  display_name: 'Accounting Assistant',
  platform: :mac
)
configure_target(
  watch_target,
  bundle_id: 'com.masein.accountingassistant.watchos',
  display_name: 'Assistant',
  platform: :watch
)

project.save
puts "Generated #{project_path}"
