# 自行部署

本项目不提供服务器。你需要管理自己的域名、TLS、磁盘、Token、AstrBot 和消息平台。

## 环境变量

| 变量 | 接收端默认 / 说明 |
| --- | --- |
| HEALTH_TIMEZONE | UTC，三端一致的 IANA 名称 |
| HEALTH_INGEST_HOST | 127.0.0.1；Dockerfile 内设置为 0.0.0.0，由宿主回环映射保护 |
| HEALTH_INGEST_PORT | 8799 |
| HEALTH_INGEST_BASE | /opt/health-ingest；存放默认 Token |
| HEALTH_INGEST_TOKEN | 默认 BASE/token，可指定 Token 文件路径 |
| HEALTH_DATA_ROOT | 接收端默认 /opt/astrbot/data/plugin_data/astrbot_plugin_apple_health；建议显式设置 |

插件的默认数据目录由 AstrBot 的 plugin_data 根目录计算；给插件进程设置 `HEALTH_DATA_ROOT` 可以覆盖。两端容器内路径可以不同，但必须映射到同一宿主目录。

## Docker

仓库 `compose.yaml` 仅启动接收端，不替换你已有 AstrBot。

- 接收端 `./runtime/health` → `/data/health`。
- 接收端 `./runtime/secrets` → `/data/secrets`。
- 给 AstrBot 增加同一宿主 `runtime/health` 的挂载。
- 两边都设置同一个 `HEALTH_TIMEZONE`，App 也填写它。
- 反向代理运行在宿主时，可访问 `127.0.0.1:8799`。如果代理在另一容器，请自行配置内部网络，不要不加限制地公开 Python HTTP 端口。
- 本机未安装 Docker 时，可以先运行纯 Python 接收端；Dockerfile 提供的是示例，不代表替你完成线上部署。

默认 UID/GID 为 10001。创建挂载目录并设置所有权后再启动，否则容器可能没有权限生成 Token。AstrBot 默认 root 容器一般可读取，但非 root 场景要统一 UID 或共享 ACL。不要把整个 plugin_data 挂载覆盖为单个健康目录。

## systemd

以下为示例路径，操作前按自己的服务器环境调整：

```bash
sudo useradd --system --home /var/lib/darkkita-health --shell /usr/sbin/nologin health
sudo install -d -o health -g health -m 700 /var/lib/darkkita-health/{health,secrets}
# 将源码部署到 /opt/darkkita-health，并确保服务用户可读。
sudo cp deploy/health-ingest.service /etc/systemd/system/
# 在 /etc/darkkita-health.env 写入 HEALTH_TIMEZONE 等环境变量。
sudo systemctl daemon-reload
sudo systemctl enable --now health-ingest
sudo journalctl -u health-ingest -n 50
```

Python 3.10+，建议 3.12+；系统需安装时区数据库。直接运行接收端不需要 pip install。

## HTTPS 与验证

为自己的域名配置可信证书，把 `deploy/nginx.conf.example` 的 location 片段合并到自己的 HTTPS server 块。示例不包含证书申请、完整站点、WAF 或公网防火墙规则。

验证顺序：

1. 宿主 `curl http://127.0.0.1:8799/health/ping` 返回 JSON。
2. 自己的域名 `https://你的域名/health/ping` 返回同样 JSON；检查 `timezone`。
3. 未带 Token 的 POST 返回 401。
4. App 填写域名、Token、同一时区，先手动上传真实数据。
5. 检查接收端生成日期文件，再检查 AstrBot `/健康` 与页面。
6. 如启用起床，配置自己的会话，实测模型与平台投递；普通上传不会触发。

不要用作者服务器、他人 Token 或真实健康数据做公开测试。项目自己的测试只使用临时数据和本机端口。

## 备份、权限与迁移

备份健康目录与密钥目录时分开保护；健康 JSON 本身未加密。更换服务器时把数据和自己的 Token 迁移到受限位置，更新 App 地址。

更换时区会改变日期边界、睡眠归档和起床防重日期，不能直接假设旧历史自动重分桶。建议确定时区后再使用；若必须更改，应自行离线迁移历史并保留备份。

只有一个接收服务进程可写共享健康目录。线程内写入有锁，**跨进程、多副本和分布式存储并发不是支持场景**。插件只更新起床事件状态，接收端不会重写已存在的同日起床事件。

## 起床任务恢复与限制

插件把事件先标记 staged，再启用 AstrBot 的一次性主 Agent 任务，避免进程崩溃后重复安排。若 staged 对应任务已不存在，保守视为已消费，不重新发。

此机制偏向避免重复打扰，可能在少数故障窗口漏掉一次问候；平台发送也不支持 exactly-once。失败请检查任务与日志，勿因客户端“已接收”误认已送达。更改机器人会话、LLM 可用性和人格属于自己的 AstrBot 配置。

插件不会自动关闭你的已有晨报，除非你显式填写 `wake_replaces_job_id`。普通主动聊天插件与静默时段不在本项目接管范围。

## 参考

- [AstrBot 插件开发](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Views](https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html)
- [Apple HealthKit](https://developer.apple.com/documentation/healthkit)
