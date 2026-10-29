# 接收协议

所有示例都是合成数据。请使用自己的 HTTPS 主机。接收端不提供公网历史读取 API。

## 鉴权、时区与限制

`POST /health/ingest` 需要：

```http
Authorization: Bearer <YOUR_OWN_TOKEN>
Content-Type: application/json
```

`GET /health/ping` 不需要 Token，返回 `ok`、`service`、`version`、`timezone`；适合状态检查，不能作为身份凭据。

`HEALTH_TIMEZONE` 为统一归档时区，默认 `UTC`，例如 `Asia/Shanghai`。客户端、服务端、AstrBot 必须一致。ISO 时间应携带 `Z` 或 UTC offset。为兼容旧快捷指令，无 offset 的时间按服务器时区解释；新客户端请始终传 offset。

| 限制 / 响应 | 含义 |
| --- | --- |
| 请求体 3,000,000 字节 | 超限 413；反向代理也需配置请求体限制 |
| 每批睡眠 5000、指标 2000、训练 300 | App 使用更小批次：500 / 500 / 100 |
| 200，`ok: true`，`saved: true` | 本批已被接收端确认；不代表 AI 消息投递成功 |
| 400 | 无效 JSON、类型、日期、单位、字段或取值 |
| 401 | Token 缺失或不匹配 |
| 404 | 路由不存在 |
| 500 | 写入或内部错误；不要当成成功 |

同一指标批次先校验所有条目，再合并；不同批次不是全局事务。遇到中断可重试，由样本键去重。Token 仅控制本服务的全部写入，不提供用户级隔离。

## 1. 睡眠

```json
{
  "type": "sleep_samples",
  "date": "2025-01-15",
  "samples": [
    {"value": "Asleep Core", "start": "2025-01-15T00:00:00Z", "end": "2025-01-15T01:00:00Z", "source": "example.watch"},
    {"value": "Asleep Deep", "start": "2025-01-15T01:00:00Z", "end": "2025-01-15T01:30:00Z", "source": "example.watch"}
  ]
}
```

`source` 可省略。`value` 支持文本别名与整数：

| 数字 | 类型 |
| --- | --- |
| 0 | InBed |
| 1 | Asleep（通用） |
| 2 | Awake |
| 3 | Asleep Core |
| 4 | Asleep Deep |
| 5 | Asleep REM |
| 6 | 通用睡眠兼容值 |

未知阶段会被拒绝，不会静默变成 0。

- 睡眠按本地中午到次日中午切分，以结束日标记。请求中的 `date` 不会强行把多个夜晚塞进同一天。
- 同来源、同起止、同类（卧床 / 睡眠阶段）的后上传条目替换旧条目；不同片段合并保留。
- 睡眠时长采用区间并集；卧床独立统计。
- 重叠睡眠阶段按 `Awake > Deep > REM > Core > generic` 切分，避免重叠累计。这是冲突处理规则，不是临床阶段判定。
- 清醒次数按清醒区间计算。没有 Awake 样本时不凭空推测夜醒次数。
- 多来源可能保留多条原始样本，因此原始 `sample_count` 不是独立睡眠片段数量；汇总时长不会直接把同时间的多个来源叠加。
- 午睡、跨中午的长睡眠可能落入下一睡眠日；这是当前归档约定。

## 2. 指标与训练

```json
{
  "type": "metrics",
  "date": "2025-01-15",
  "metrics": [
    {"metric": "heart_rate", "value": 60, "unit": "bpm", "start": "2025-01-15T08:00:00Z", "end": "2025-01-15T08:00:00Z", "source": "example.watch"},
    {"metric": "steps", "value": 5000, "unit": "count", "start": "2025-01-15T00:00:00Z", "end": "2025-01-15T23:59:59Z", "source": "HealthKit Daily Aggregate"}
  ],
  "workouts": [
    {"activity": "walking", "start": "2025-01-15T10:00:00Z", "end": "2025-01-15T10:30:00Z", "distance_km": 2, "energy_kcal": 100, "source": "example.watch"}
  ]
}
```

可只上传 `metrics` 或只上传 `workouts`，但不能同时为空。训练可省略能量与距离，不能将缺失值伪造成 0。

| metric | 推荐单位 | 额外接受 |
| --- | --- | --- |
| heart_rate | bpm | count/min |
| resting_heart_rate | bpm | count/min |
| hrv | ms | — |
| respiratory_rate | breaths/min | rpm |
| oxygen_saturation | % | percent |
| wrist_temperature | °C | c / degc |
| steps | count | steps，必须非负整数 |
| active_energy | kcal | — |
| distance | km | mi，转换为 km |
| exercise_minutes | min | minutes |
| stand_minutes | min | minutes |

部分中文单位别名用于旧客户端兼容，以代码校验为准。血氧要求 0–100；数值必须有限。起止顺序错误、未知指标、单位、缺 value 均拒绝。

- 指标按 `metric + source + start + end` 去重，同键后上传替换。
- 指标起止时间都必须落在请求 `date` 的服务器本地日内。外部客户端应先处理跨日样本。
- iOS 对跨午夜的离散测量按结束日归档，并将开始时间限制到该日午夜；这尤其适用于整夜腕温，不声称保留了原始跨日开始时间。
- iOS 对步数、活动能量、距离、锻炼/站立分钟使用 `HKStatisticsQuery(.cumulativeSum)` 得到每日统计。上传固定的午夜到 23:59:59 日期键，后续总量替换原值；当天未结束时日终端点是**统计桶边界**。
- 外部原始累计样本按来源分别处理；同来源无重叠才求和，优先使用整日总量，跨来源保守选一个有效流，不保证复制 Apple 的完整来源优先级合并算法。
- 训练按起止、类型与来源去重，开始日必须与请求日期一致。未知 HealthKit 训练类型由 App 保留为 `healthkit_<数字>`。

## 3. 旧步数格式

```json
{"type": "steps", "date": "2025-01-15", "steps": 5000}
```

旧客户端仍可使用。日总量不会当成新增增量每次相加。

## 4. 起床事件

```json
{"type": "wake_event", "occurred_at": "2025-01-15T08:00:00Z", "holiday_mode": true}
```

请用当前真实时间代替示例时间。

- 需同一 Bearer Token；先完成普通健康上传，再发送事件。
- 事件时间与接收端当前时间相差不能超过 15 分钟；最近 15 分钟须有健康历史写入。
- `holiday_mode` 为布尔值，省略默认 false。
- 日期从事件时间按服务器时区计算，不能由客户端任意指定。
- 同一个本地日期只创建一个事件。首次返回 `queued: true`、`delay_seconds: 180`；重复返回 `duplicate: true`、`queued: false`。
- `queued` 代表事件文件被保存；即便插件未启用也不代表已安排或投递。要发送消息，必须启用插件起床设置并配置自己的目标会话。
- 接收事件不修改普通上传语义，不会由睡眠样本的结束时间自动推断用户醒来。

## 磁盘布局

```text
HEALTH_DATA_ROOT/
  health.json                 兼容的最新摘要
  history/YYYY-MM-DD.json     日期历史
  wake_events/YYYY-MM-DD.json 起床事件、任务 ID 与状态
  wake_settings.json          可选旧式设置；新部署推荐插件配置界面
```

历史为增量合并，未实现删除同步或自动保留期限。请自行设计备份、清理与删除策略。不要把真实记录作为测试 fixture 提交。

## AstrBot 侧工具

```json
{"section":"all","date":"2025-01-15","history_days":0}
```

`get_health_summary`：`section` 可为 `all | sleep | steps | metrics | workouts | history`；`date` 可选；`history_days > 0` 返回最近日期摘要。无日期的特定类别会回退到最近有该类别的日期，并标明数据日期，避免把旧数据冒充昨晚。

WebUI 的 `overview` / `day` 是 AstrBot 插件扩展 API，继承其鉴权，不是公网接收端接口。`day` 附带 `metric_summaries` 供页面使用，与 AI 摘要保持累计口径一致。
