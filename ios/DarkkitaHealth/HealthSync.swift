import Foundation
import HealthKit
import Security

struct SyncFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

final class NoRedirectDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

enum TokenStore {
    private static let service = "org.darkkita.health"
    static func read() -> String {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
            kSecAttrAccount as String: "upload-token", kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }
    static func save(_ token: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                  kSecAttrAccount as String: "upload-token"]
        let attrs: [String: Any] = [kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, attrs as CFDictionary)
        if status == errSecItemNotFound {
            var insert = query
            attrs.forEach { insert[$0.key] = $0.value }
            guard SecItemAdd(insert as CFDictionary, nil) == errSecSuccess else { throw SyncFailure(message: "无法保存 Token 到钥匙串") }
        } else if status != errSecSuccess { throw SyncFailure(message: "无法更新钥匙串 Token") }
    }
}

enum ServerSettings {
    static var timezone: String { UserDefaults.standard.string(forKey: "serverTimezone") ?? TimeZone.current.identifier }
    static func endpoint() throws -> URL {
        let raw = UserDefaults.standard.string(forKey: "serverEndpoint") ?? ""
        guard let url = URL(string: raw), url.scheme?.lowercased() == "https", url.host != nil,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else {
            throw SyncFailure(message: "请先保存你自己的 HTTPS 上传接口地址")
        }
        return url
    }
    static func save(endpoint: String, timezone: String) throws {
        let raw = endpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: raw), url.scheme?.lowercased() == "https", url.host != nil,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.hasSuffix("/health/ingest") else { throw SyncFailure(message: "请填写 https://你的域名/health/ingest") }
        guard TimeZone(identifier: timezone) != nil else { throw SyncFailure(message: "时区需为有效 IANA 名称，如 UTC、Asia/Shanghai") }
        UserDefaults.standard.set(url.absoluteString, forKey: "serverEndpoint")
        UserDefaults.standard.set(timezone, forKey: "serverTimezone")
    }
}

struct MetricSpec {
    let identifier: HKQuantityTypeIdentifier
    let name: String
    let unit: HKUnit
    let wireUnit: String
    var cumulative = false
    var multiplier = 1.0
    var type: HKQuantityType { HKObjectType.quantityType(forIdentifier: identifier)! }
}

actor HealthSync {
    static let shared = HealthSync()
    private let store = HKHealthStore()
    private var running = false
    static var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: ServerSettings.timezone) ?? .gmt
        return c
    }
    static let specs: [MetricSpec] = [
        .init(identifier: .heartRate, name: "heart_rate", unit: .count().unitDivided(by: .minute()), wireUnit: "bpm"),
        .init(identifier: .restingHeartRate, name: "resting_heart_rate", unit: .count().unitDivided(by: .minute()), wireUnit: "bpm"),
        .init(identifier: .heartRateVariabilitySDNN, name: "hrv", unit: .secondUnit(with: .milli), wireUnit: "ms"),
        .init(identifier: .respiratoryRate, name: "respiratory_rate", unit: .count().unitDivided(by: .minute()), wireUnit: "breaths/min"),
        .init(identifier: .oxygenSaturation, name: "oxygen_saturation", unit: .percent(), wireUnit: "%", multiplier: 100),
        .init(identifier: .appleSleepingWristTemperature, name: "wrist_temperature", unit: .degreeCelsius(), wireUnit: "°C"),
        .init(identifier: .stepCount, name: "steps", unit: .count(), wireUnit: "count", cumulative: true),
        .init(identifier: .activeEnergyBurned, name: "active_energy", unit: .kilocalorie(), wireUnit: "kcal", cumulative: true),
        .init(identifier: .distanceWalkingRunning, name: "distance", unit: .meterUnit(with: .kilo), wireUnit: "km", cumulative: true),
        .init(identifier: .appleExerciseTime, name: "exercise_minutes", unit: .minute(), wireUnit: "min", cumulative: true),
        .init(identifier: .appleStandTime, name: "stand_minutes", unit: .minute(), wireUnit: "min", cumulative: true)
    ]
    static var readTypes: Set<HKObjectType> {
        var types = Set<HKObjectType>(specs.map { $0.type })
        types.insert(HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!)
        types.insert(HKObjectType.workoutType())
        return types
    }
    func authorize() async throws {
        guard HKHealthStore.isHealthDataAvailable() else { throw SyncFailure(message: "此设备不支持健康数据") }
        try await store.requestAuthorization(toShare: [], read: Self.readTypes)
        UserDefaults.standard.set(true, forKey: "authorizationRequested")
    }
    private func samples(_ type: HKSampleType, from start: Date, to end: Date) async throws -> [HKSample] {
        try await withCheckedThrowingContinuation { continuation in
            let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: [])
            let query = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit,
                sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)]) { _, samples, error in
                if let error { continuation.resume(throwing: error) }
                else { continuation.resume(returning: samples ?? []) }
            }
            store.execute(query)
        }
    }
    private func dailySum(_ spec: MetricSpec, from start: Date, to end: Date) async throws -> Double? {
        try await withCheckedThrowingContinuation { continuation in
            let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: .strictStartDate)
            let query = HKStatisticsQuery(quantityType: spec.type, quantitySamplePredicate: predicate, options: .cumulativeSum) { _, stats, error in
                if let error {
                    if (error as NSError).domain == HKErrorDomain && (error as NSError).code == HKError.Code.errorNoData.rawValue {
                        continuation.resume(returning: nil)
                    } else { continuation.resume(throwing: error) }
                } else { continuation.resume(returning: stats?.sumQuantity()?.doubleValue(for: spec.unit)) }
            }
            store.execute(query)
        }
    }
    @discardableResult
    private func post(_ payload: [String: Any], token: String) async throws -> [String: Any] {
        let url = try ServerSettings.endpoint()
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 45
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        let session = URLSession(configuration: .ephemeral, delegate: NoRedirectDelegate(), delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw SyncFailure(message: "服务器没有返回 HTTP 响应") }
        guard (200..<300).contains(http.statusCode) else {
            // Never display the request or credentials in errors.
            throw SyncFailure(message: "服务器返回 HTTP \(http.statusCode)，请检查 Token 或接口。已成功的批次可安全重传。")
        }
        guard let body = try JSONSerialization.jsonObject(with: data) as? [String: Any], body["ok"] as? Bool == true else {
            throw SyncFailure(message: "服务器未确认保存，不能视为上传成功")
        }
        return body
    }
    func upload() async throws -> String {
        guard !running else { throw SyncFailure(message: "已有一次上传正在进行，请稍后再试") }
        running = true
        defer { running = false }
        let token = TokenStore.read().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { throw SyncFailure(message: "请先打开 Darkkita Health 保存上传 Token") }
        guard UserDefaults.standard.bool(forKey: "authorizationRequested") else {
            throw SyncFailure(message: "请先打开 App，点击授权读取健康数据")
        }
        _ = try ServerSettings.endpoint()
        let now = Date(), calendar = Self.calendar
        let today = calendar.startOfDay(for: now)
        let start = calendar.date(byAdding: .day, value: -2, to: today)!
        let iso = ISO8601DateFormatter()
        let date = DateFormatter()
        date.calendar = calendar; date.locale = Locale(identifier: "en_US_POSIX")
        date.timeZone = calendar.timeZone; date.dateFormat = "yyyy-MM-dd"
        var metrics: [String: [[String: Any]]] = [:]
        var workouts: [String: [[String: Any]]] = [:]
        var sleepRows: [[String: Any]] = []
        var warnings: [String] = []
        // Collect before uploading so a locked HealthKit store doesn't produce a misleading partial success.
        let sleepType = HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!
        let sleep = try await samples(sleepType, from: start, to: now)
        for case let sample as HKCategorySample in sleep where sample.endDate <= now {
            sleepRows.append(["value": sample.value, "start": iso.string(from: sample.startDate),
                "end": iso.string(from: sample.endDate), "source": sample.sourceRevision.source.bundleIdentifier])
        }
        for spec in Self.specs {
            if spec.cumulative {
                for offset in -2...0 {
                    let day = calendar.date(byAdding: .day, value: offset, to: today)!
                    let next = calendar.date(byAdding: .day, value: 1, to: day)!
                    let until = min(next, now)
                    if let total = try await dailySum(spec, from: day, to: until), total.isFinite {
                        let key = date.string(from: day)
                        metrics[key, default: []].append(["metric": spec.name,
                            "value": spec.name == "steps" ? total.rounded() : total,
                            "unit": spec.wireUnit, "start": iso.string(from: day),
                            // Stable day endpoints let the server replace a total rather than accumulate snapshots.
                            "end": iso.string(from: next.addingTimeInterval(-1)), "source": "HealthKit Daily Aggregate"])
                    }
                }
            } else {
                for case let sample as HKQuantitySample in try await samples(spec.type, from: start, to: now) where sample.endDate <= now {
                    let value = sample.quantity.doubleValue(for: spec.unit) * spec.multiplier
                    guard value.isFinite else { continue }
                    let key = date.string(from: sample.endDate)
                    // Server requires timestamps within one server-local date. An overnight discrete measurement is assigned to its end date.
                    let sampleStart = max(sample.startDate, calendar.startOfDay(for: sample.endDate))
                    metrics[key, default: []].append(["metric": spec.name, "value": value, "unit": spec.wireUnit,
                        "start": iso.string(from: sampleStart), "end": iso.string(from: sample.endDate),
                        "source": sample.sourceRevision.source.bundleIdentifier])
                }
            }
        }
        for case let workout as HKWorkout in try await samples(HKObjectType.workoutType(), from: start, to: now) where workout.endDate <= now {
            var row: [String: Any] = ["activity": Self.activityName(workout.workoutActivityType),
                "start": iso.string(from: workout.startDate), "end": iso.string(from: workout.endDate),
                "source": workout.sourceRevision.source.bundleIdentifier]
            if let energy = workout.statistics(for: HKQuantityType(.activeEnergyBurned))?.sumQuantity() {
                row["energy_kcal"] = energy.doubleValue(for: .kilocalorie())
            }
            if let distance = workout.totalDistance { row["distance_km"] = distance.doubleValue(for: .meterUnit(with: .kilo)) }
            workouts[date.string(from: workout.startDate), default: []].append(row)
        }
        var uploaded = 0, batches = 0
        do {
            for chunk in sleepRows.chunked(500) {
                try await post(["type": "sleep_samples", "date": date.string(from: now), "samples": chunk], token: token)
                uploaded += chunk.count; batches += 1
            }
            for day in Set(metrics.keys).union(workouts.keys).sorted() {
                for chunk in (metrics[day] ?? []).chunked(500) {
                    try await post(["type": "metrics", "date": day, "metrics": chunk], token: token)
                    uploaded += chunk.count; batches += 1
                }
                for chunk in (workouts[day] ?? []).chunked(100) {
                    try await post(["type": "metrics", "date": day, "metrics": [], "workouts": chunk], token: token)
                    uploaded += chunk.count; batches += 1
                }
            }
        } catch {
            throw SyncFailure(message: "已确认 \(batches) 批 / \(uploaded) 条。\(error.localizedDescription)")
        }
        guard uploaded > 0 else { throw SyncFailure(message: "没有读到可上传数据。请确认已允许读取、手表已同步，并解锁手机；未授权与无记录在 HealthKit 中都可能返回空结果。") }
        if sleepRows.isEmpty { warnings.append("未读到睡眠记录") }
        let message = "上传成功：\(uploaded) 条，\(batches) 批（今天及前两天）" + (warnings.isEmpty ? "" : "；" + warnings.joined(separator: "；"))
        UserDefaults.standard.set(now, forKey: "lastUpload")
        UserDefaults.standard.set(message, forKey: "lastResult")
        return message
    }
    func notifyWake(holidayMode: Bool) async throws -> String {
        let token = TokenStore.read().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { throw SyncFailure(message: "请先保存上传 Token") }
        let response = try await post(["type": "wake_event", "occurred_at": ISO8601DateFormatter().string(from: Date()),
                        "holiday_mode": holidayMode], token: token)
        if response["duplicate"] as? Bool == true { return "今天已记录起床，不再重复安排问候" }
        return "已通知服务器起床，约3分钟后主动联系你"
    }
    private static func activityName(_ value: HKWorkoutActivityType) -> String {
        switch value {
        case .running: return "running"
        case .walking: return "walking"
        case .cycling: return "cycling"
        case .swimming: return "swimming"
        case .hiking: return "hiking"
        case .yoga: return "yoga"
        case .traditionalStrengthTraining: return "strength_training"
        case .functionalStrengthTraining: return "functional_strength_training"
        case .highIntensityIntervalTraining: return "hiit"
        case .elliptical: return "elliptical"
        case .rowing: return "rowing"
        case .dance: return "dance"
        default: return "healthkit_\(value.rawValue)"
        }
    }
}

extension Array {
    func chunked(_ count: Int) -> [[Element]] {
        stride(from: 0, to: self.count, by: count).map { Array(self[$0..<Swift.min($0 + count, self.count)]) }
    }
}
