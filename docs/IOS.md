# iOS、快捷指令与发布构建

## 源码运行

打开 `ios/DarkkitaHealth.xcodeproj`，选择自己的 Team、唯一 Bundle Identifier 和已连接 iPhone。保留 `com.apple.developer.healthkit` entitlement，系统首次运行会要求健康读取授权；App 不请求写入健康数据。

仓库的 Team 为空，没有作者账号的证书、私钥或描述文件。能否启用某项 capability、描述文件有效期和设备限制取决于自己的 Apple 开发账号。不要认为所有免费或第三方签名都支持 HealthKit。

当前 Release 用 Xcode 27 / iOS SDK 27 构建、最低部署 iOS 17；较旧 Xcode 能否重新构建取决于其 SwiftUI/App Intents SDK 支持，若编译失败请使用更新的 Xcode。

## 未签名 IPA

未签名 IPA 是可以重签名的编译产物，**不是免签安装包**。App 的 HealthKit 权限必须与自己的签名描述文件匹配。推荐用 Xcode 运行源码，避免外部重签工具丢失 entitlement。

App 首次使用没有预设服务器地址，不会连接作者的接口。先填写自己的 HTTPS 上传路径、IANA 时区、Token，并手动授权与上传。

## 模拟器

```bash
unzip DarkkitaHealth-simulator.zip
xcrun simctl install booted DarkkitaHealth.app
```

需要兼容的 iOS 模拟器运行时。模拟器只用于页面、App Intents 等开发验证，不代表有真实手表样本或真机锁屏行为。

## 复现构建

在项目根目录：

```bash
scripts/build-ios.sh
```

可通过 `BUILD_DIR`、`DIST_DIR` 环境变量覆盖构建和产物目录。构建不使用开发签名；打包脚本检测到 `embedded.mobileprovision` 或 `_CodeSignature` 时拒绝打包，防止误发布个人签名。

未签名构建、插件 ZIP 与 SHA-256 文件通过 GitHub Releases 分发，源码不会提交二进制大文件或私人配置。你也可以在 GitHub Actions 手动运行 Build iOS workflow 获得自己的构建 artifact。

## 配置与密钥

- 上传 Token 位于 app 本机钥匙串，`AfterFirstUnlockThisDeviceOnly`；不会作为源码或默认配置发布。
- 地址、时区、假期模式、最后结果存于 UserDefaults。
- Token 输入支持粘贴裸 Token，或 `Bearer ` 前缀；界面不会回显已保存 Token。
- 更换域名前，请确认新主机是你管理或信任的目标。App 不跟随健康上传的重定向。
- 卸载可能保留钥匙串项，具体由 iOS 行为决定；共享设备时请自行管理钥匙串和安装配置。

## 两个动作

**上传健康数据**：查询今天及前两天的数据并分批写入服务端，适合晚间定时。

**起床并上传健康数据**：先执行相同上传，成功后发送起床事件。问候默认在 AstrBot 插件中关闭；没有完成服务端配置时，不应把客户端的“事件已接收”理解成 AI 已经收到或发消息。

App 不创建个人自动化，也不保证自己常驻后台。App Intent `openAppWhenRun=false` 只是运行方式，不代表绕过锁屏健康数据保护。

## 睡眠专注关闭的触发

Apple 的 Sleep → Waking Up 是起床闹钟事件，不能一概当作“手动关闭睡眠专注”。请查看自己的 iOS 自动化可选项并实测。如果没有对应 Focus 关闭触发，可以用一个快捷指令同时关闭睡眠专注并执行起床动作。

[Apple 事件触发说明](https://support.apple.com/en-ie/guide/shortcuts/apd932ff833f/ios)

## 数据边界

- HealthKit 可能因权限或数据保护返回空结果；App 无法可靠区分未授权与无记录。
- 累计样本使用 HealthKit 统计，不能单纯把手机与手表所有记录直接相加。
- 当日数据仍会随手表同步更新，次日/后日补传可更新每日快照。
- 当前实现一次读取三天的离散记录再分批上传；大量样本可能受 App Intents 运行时间和内存限制，超大历史回灌不属于本版范围。
- 不含任何 ECG、位置路线或全量 HealthKit 导出功能。
