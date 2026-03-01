# Nextcloud Talk Channel for CoPaw

完整的 Nextcloud Talk (Spreed) Bot 集成，支持通过 Webhook 接收消息并通过 API 发送回复。

## 功能特性

- ✅ **接收聊天消息** - 通过 Webhook 接收 Nextcloud Talk 消息
- ✅ **发送消息** - 通过 Bot API 发送文本/Markdown 消息
- ✅ **安全验证** - HMAC-SHA256 签名验证所有 webhook 请求
- ✅ **会话管理** - 支持会话状态保存和跨消息上下文
- ✅ **表情反应** - 支持接收消息的表情反应（通过 feature 标志）
- ✅ **Markdown 支持** - 自动处理 Markdown 格式消息
- ✅ **提及解析** - 解析和替换 @提及 和 @呼叫 等占位符

## 系统需求

### Nextcloud 服务器

- Nextcloud >= 27.1（支持 `bots-v1` capability）
- Talk 17.1+（支持 Bot API）

### Python 依赖

**无需额外安装依赖！** 本 channel 使用 Python 标准库实现 HTTP webhook 服务器：

- ✅ `http.server` - Python 内置 HTTP 服务器
- ✅ ` threading` - 后台线程运行
- ✅ `json` - 消息解析
- ✅ `aiohttp` - 已在其他 channels（Feishu）中使用，用于发送 HTTP 请求

```bash
# 无需安装额外包，仅确认 aiohttp 已安装（通常已存在）
pip check aiohttp
```

### 权限要求

- **Bot 安装**：需要 Nextcloud 管理员权限（运行 OCC 命令）
- **Bot 使用**：普通用户即可，在聊天中添加 bot 即可

## 快速开始

### 1. 生成共享密钥

```bash
openssl rand -hex 32
# 输出: abc123def456... (32字节十六进制字符串)
```

### 2. 安装 Bot（需要 Nextcloud 管理员权限）

SSH 到 Nextcloud 服务器：

```bash
cd /var/www/nextcloud
sudo -u www-data php occ talk:bot:install \
  --name="CoPaw Assistant" \
  --url="https://your-server.com/webhook/nextcloud_talk" \
  --secret="abc123def456..." \
  --description="Personal AI assistant" \
  --state=enabled \
  --feature=bots-v1
```

命令会返回 bot token，记录下来！

### 3. 配置 CoPaw

编辑 `~/.copaw/config.json`：

```json
{
  "channels": {
    "nextcloud_talk": {
      "enabled": true,
      "webhook_secret": "abc123def456...",
      "webhook_host": "0.0.0.0",
      "webhook_port": 8765,
      "webhook_path": "/webhook/nextcloud_talk",
      "bot_prefix": "[BOT] "
    }
  }
}
```

### 4. 配置反向代理（Nginx）

```nginx
location = /webhook/nextcloud_talk {
    proxy_pass http://localhost:8765/webhook/nextcloud_talk;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Nextcloud-Talk-Backend https://$host;
}
```

重要：`X-Nextcloud-Talk-Backend` header 必须设置！

### 5. 重启 CoPaw

```bash
copaw app restart
```

### 6. 测试

在 Nextcloud Talk 中：
1. 打开一个聊天
2. 添加 bot（CoPaw Assistant）
3. 发送消息："你好"
4. 应该收到回复！

## 配置选项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | false | 启用 channel |
| `webhook_secret` | string | "" | 共享密钥（必需） |
| `webhook_host` | string | "0.0.0.0" | Webhook 服务器监听地址 |
| `webhook_port` | int | 8765 | Webhook 服务器端口 |
| `webhook_path` | string | "/webhook/nextcloud_talk" | Webhook 路径 |
| `bot_prefix` | string | "[BOT] " | Bot 消息前缀 |

## 本地开发（使用 ngrok）

如果本地测试没有公网服务器：

```bash
# 安装 ngrok
brew install ngrok  # macOS

# 启动隧道
ngrok http 8765

# 得到公网 URL，例如: https://abcd1234.ngrok.io
```

然后用 ngrokURL 配置 bot：
```bash
--url="https://abcd1234.ngrok.io/webhook/nextcloud_talk"
```

## 架构说明

### Webhook 流程

```
Nextcloud Talk
    ↓ (POST webhook with HMAC signature)
Python stdlib HTTP Server (handler_stdlib.py)
    ↓ (verify signature)
Handler (handler_stdlib.py)
    ↓ (parse Activity Streams)
Channel Manager enqueue
    ↓ (AgentRequest)
CoPaw Agent
    ↓ (AgentResponse)
Nextcloud Talk API (send via bot token)
```

### 关键组件

- **channel.py** - 主 channel 类，定义 `start()`、`stop()`、`send()` 方法
- **handler_stdlib.py** - **Python 标准库** HTTP webhook 处理器，验证签名并解析消息（无 FastAPI 依赖）
- **content_utils.py** - Nextcloud Talk Activity Streams 消息解析
- **utils.py** - 签名验证、URL 构建、token 存储等工具
- **constants.py** - 常量定义（header 名称、activity 类型等）
- **handler.py** - （已弃用）原始 FastAPI 实现，保留供参考

### Activity Streams 消息格式

Nextcloud Talk 使用 Activity Streams 2.0 标准：

```json
{
  "type": "Create",
  "actor": {
    "type": "Person",
    "id": "users/username",
    "name": "Display Name"
  },
  "object": {
    "type": "Note",
    "id": "1567",
    "name": "message",
    "content": "{\"message\":\"hi!\",\"parameters\":{}}",
    "mediaType": "text/markdown"
  },
  "target": {
    "type": "Collection",
    "id": "conversation-token",
    "name": "Room Name"
  }
}
```

## 故障排查

### Bot 不响应

1. 检查 CoPaw 日志：
   ```bash
   copaw logs | grep nextcloud_talk
   ```

2. 验证 webhook 端点：
   ```bash
   curl https://your-server.com/webhook/nextcloud_talk
   ```

3. 确认 secret 一致：
   - OCC 命令中的 `--secret` 参数
   - `config.json` 中的 `webhook_secret` 必须完全一致

### 无法发送消息

1. 检查 bot token 是否正确保存
2. 确认 bot 已添加到目标聊天
3. 检查 `X-Nextcloud-Talk-Backend` header

### 权限问题

如果没有 Nextcloud 管理员权限：
- 联系管理员协助安装 bot
- 或者自己部署 Nextcloud 实例

## 安全建议

1. ✅ **使用 HTTPS** - Webhook 必须是 HTTPS
2. ✅ **强密钥** - Secret 至少 32 字节，随机生成
3. ✅ **验证签名** - 所有请求都会验证 HMAC-SHA256
4. ✅ **限制访问** - 防火墙只允许 Nextcloud 服务器 IP

## Bot 管理命令

### 列出所有 Bot

```bash
sudo -u www-data php occ talk:bot:list
```

### 查看 Bot 信息

```bash
sudo -u www-data php occ talk:bot:info <TOKEN>
```

### 删除 Bot

```bash
sudo -u www-data php occ talk:bot:remove <TOKEN>
```

### 更新 Bot

```bash
# 删除旧 bot
sudo -u www-data php occ talk:bot:remove <TOKEN>

# 重新安装（使用相同 URL 和 secret）
sudo -u www-data php occ talk:bot:install \
  --name="CoPaw Assistant" \
  --url="https://your-server.com/webhook/nextcloud_talk" \
  --secret="YOUR_SECRET" \
  --description="Personal AI assistant" \
  --state=enabled \
  --feature=bots-v1
```

## 高级功能

### 启用表情反应

重新安装 bot，添加 `reaction` feature：

```bash
sudo -u www-data php occ talk:bot:install \
  --name="CoPaw Assistant" \
  --url="https://your-server.com/webhook/nextcloud_talk" \
  --secret="YOUR_SECRET" \
  --description="Personal AI assistant" \
  --state=enabled \
  --feature=bots-v1 \
  --feature=reaction
```

### 会话持久化

Channel 会自动保存 conversation token 和 backend URL，支持：
- 跨消息保持上下文
- Cron 定时任务发送消息到特定对话

## API 文档

完整 API 文档：https://nextcloud-talk.readthedocs.io/en/latest/bots/

### 发送消息（手动测试）

```bash
curl -X POST \
  "https://nextcloud.example.com/ocs/v2.php/apps/spreed/api/v1/bot/TOKEN/message" \
  -H "Content-Type: application/json" \
  -H "OCS-APIRequest: true" \
  -H "X-Nextcloud-Talk-Bot-Random: RANDOM" \
  -H "X-Nextcloud-Talk-Bot-Signature: SIGNATURE" \
  -d '{"message":"Hello from CoPaw!"}'
```

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

与 CoPaw 主项目相同

## 相关链接

- [Nextcloud Talk 主页](https://nextcloud.com/talk/)
- [Bot API 文档](https://nextcloud-talk.readthedocs.io/en/latest/bots/)
- [CoPaw 文档](https://github.com/...)
