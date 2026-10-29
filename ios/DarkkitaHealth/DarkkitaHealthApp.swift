import SwiftUI
import AppIntents

@main
struct DarkkitaHealthApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct UploadHealthIntent: AppIntent {
    static var title: LocalizedStringResource = "上传健康数据"
    static var description = IntentDescription("将今天及前两天的睡眠、心率、活动与训练上传至你的健康服务器。请先在 App 中保存 Token 并授权。")
    static var openAppWhenRun: Bool = false
    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        do {
            let message = try await HealthSync.shared.upload()
            return .result(value: message, dialog: IntentDialog(stringLiteral: message))
        } catch {
            UserDefaults.standard.set(error.localizedDescription, forKey: "lastResult")
            throw error
        }
    }
}

struct WakeAndUploadIntent: AppIntent {
    static var title: LocalizedStringResource = "起床并上传健康数据"
    static var description = IntentDescription("用于起床快捷指令：先上传，成功后通知 AI，约3分钟后问候，每天一次。")
    static var openAppWhenRun: Bool = false
    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let result = try await HealthSync.shared.upload()
        do {
            let holiday = UserDefaults.standard.object(forKey: "holidayMode") as? Bool ?? false
            let notice = try await HealthSync.shared.notifyWake(holidayMode: holiday)
            let message = result + "；" + notice
            UserDefaults.standard.set(message, forKey: "lastResult")
            return .result(value: message, dialog: IntentDialog(stringLiteral: message))
        } catch {
            let message = "健康数据已上传，但起床通知失败：" + error.localizedDescription + "。可重新运行起床动作。"
            UserDefaults.standard.set(message, forKey: "lastResult")
            throw SyncFailure(message: message)
        }
    }
}

struct HealthShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: WakeAndUploadIntent(), phrases: ["用\(.applicationName)报告起床"],
                    shortTitle: "起床并上传健康数据", systemImageName: "sun.max")
        AppShortcut(intent: UploadHealthIntent(), phrases: ["用\(.applicationName)上传健康数据", "Upload health with \(.applicationName)"],
                    shortTitle: "上传健康数据", systemImageName: "heart.circle")
    }
}

struct ContentView: View {
    @AppStorage("holidayMode") private var holidayMode = false
    @State private var endpoint = UserDefaults.standard.string(forKey: "serverEndpoint") ?? ""
    @State private var timezone = ServerSettings.timezone
    @State private var token = ""
    @State private var busy = false
    @State private var message = UserDefaults.standard.string(forKey: "lastResult") ?? "首次使用：保存 Token → 授权 → 上传"
    @State private var hasToken = !TokenStore.read().isEmpty
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label("DARKKITA · APPLE HEALTH", systemImage: "heart.fill").foregroundStyle(.pink)
                    Text("睡眠、心率、血氧、HRV、呼吸、腕温、步数、活动能量、步行跑步距离、锻炼与站立分钟，以及训练记录。没有的数据不补零。")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Section("连接你的服务器") {
                    TextField("https://health.example.com/health/ingest", text: $endpoint)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("服务器时区，如 UTC / Asia/Shanghai", text: $timezone)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("保存服务器设置") {
                        do { try ServerSettings.save(endpoint: endpoint, timezone: timezone); message = "服务器设置已保存，请确认三端时区一致" }
                        catch { message = error.localizedDescription }
                    }
                    Text("接口、AstrBot 与 App 使用相同 IANA 时区，防止跨天归档错误。")
                        .font(.footnote).foregroundStyle(.secondary)
                    SecureField(hasToken ? "已保存 Token，输入可替换" : "粘贴上传 Token（不用加 Bearer）", text: $token)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button(hasToken ? "更新 Token" : "保存 Token") {
                        do {
                            var value = token.trimmingCharacters(in: .whitespacesAndNewlines)
                            if value.lowercased().hasPrefix("bearer ") { value = String(value.dropFirst(7)).trimmingCharacters(in: .whitespacesAndNewlines) }
                            guard !value.isEmpty, !value.contains(where: { $0.isWhitespace }) else { throw SyncFailure(message: "Token 不能为空或包含空白") }
                            try TokenStore.save(value); token = ""; hasToken = true; message = "Token 已安全保存到本机钥匙串"
                        } catch { message = error.localizedDescription }
                    }.disabled(token.isEmpty)
                }
                Section("健康数据") {
                    Button("1. 授权读取健康数据", systemImage: "checkmark.shield") {
                        run { try await HealthSync.shared.authorize(); return "授权请求已完成。只会读取你允许的项目。" }
                    }
                    Button("2. 立即上传", systemImage: "arrow.up.circle.fill") {
                        run { try await HealthSync.shared.upload() }
                    }.disabled(!hasToken)
                    if busy { ProgressView("正在处理…") }
                    Text(message).font(.footnote).textSelection(.enabled)
                }.disabled(busy)
                Section("起床后主动问候") {
                    Toggle("假期 / Reading Week 模式", isOn: $holidayMode)
                    Text("开启时，起床问候不按星期推测上课或催你出门。恢复上课后可关闭。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("起床触发 → 运行「起床并上传健康数据」。上传成功后，服务器约3分钟后让 AI 来找你，每天一次。普通「上传健康数据」不会触发问候。")
                        .font(.footnote)
                }
                Section("快捷指令自动化") {
                    Text("添加动作 → 搜索 Darkkita Health → 上传健康数据。每天早晚各运行一次同一个动作即可。")
                    Text("不固定起床时间：睡眠专注关闭时用起床动作，晚上自行设定时间用普通上传动作。每次补传今天及前两天，失败后可重试。")
                    Text("首次授权请在此 App 内完成。锁屏可能导致健康数据不可读，失败时请解锁重试；定时触发不代表 iOS 保证每次成功。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("隐私") {
                    Text("仅向你配置的固定 HTTPS 服务发送健康数据。Token 存在钥匙串；App 不调用 AI、不含广告、不写入健康数据。统计类指标使用 HealthKit 汇总，避免直接叠加手机与手表样本。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Darkkita Health")
            .tint(.pink)
            .onChange(of: scenePhase) { _, phase in
                if phase == .active, let result = UserDefaults.standard.string(forKey: "lastResult") { message = result }
            }
        }
    }
    private func run(_ action: @escaping () async throws -> String) {
        busy = true
        Task { @MainActor in
            defer { busy = false }
            do { message = try await action() }
            catch { message = error.localizedDescription }
            UserDefaults.standard.set(message, forKey: "lastResult")
        }
    }
}
