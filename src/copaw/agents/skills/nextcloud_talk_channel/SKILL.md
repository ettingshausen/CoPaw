---
name: nextcloud_talk_channel
description: "Setup Nextcloud Talk bot integration for CoPaw. Helps users install and configure Nextcloud Talk bots via command line, configure webhook URLs, and set up the CoPaw nextcloud_talk channel."
metadata:
  {
    "copaw":
      {
        "emoji": "☁️",
        "requires": {}
      }
  }
---

# Nextcloud Talk Channel Setup

此 skill 用于帮助用户配置 Nextcloud Talk (Spreed) bot 集成到 CoPaw。

## 前置条件

1. **Nextcloud 服务器** - 需要有 Nextcloud 实例，版本 >= 27.1（支持 bots-v1 capability）
2. **Nextcloud 管理员权限** - 需要能够 SSH 到服务器或访问控制台运行 OCC 命令
3. **Python 依赖** - **无需额外安装依赖！** 本 channel 使用 Python 标准库实现：

```bash
# 仅需确认 aiohttp 已安装（通常已存在，其他 channels 已使用）
pip check aiohttp

# 如果缺失：
pip install aiohttp
```

## 安装流程

### 步骤 1：配置 webhook URL

决定 webhook 端点：

- 公网可访问的服务器 URL（例如：`https://your-server.com`）
- 本地开发可使用内网穿透工具（ngrok、frp 等）

假设选择的 URL 为：`https://your-server.com`

webhook 完整路径将是：`https://your-server.com/webhook/nextcloud_talk`

### 步骤 2：选择共享密钥（Secret）

选择一个强随机字符串作为 webhook 签名验证密钥：

```bash
# 生成随机密钥
openssl rand -hex 32
```

记录下来这个 secret，后面配置需要用到。

### 步骤 3：在 Nextcloud 服务器安装 Bot

SSH 到 Nextcloud 服务器，运行 OCC 命令：

```bash
# 进入 Nextcloud 目录
cd /var/www/nextcloud  # 路径可能不同

# 运行 bot 安装命令
sudo -u www-data php occ talk:bot:install \
  --name="CoPaw Assistant" \
  --url="https://your-server.com/webhook/nextcloud_talk" \
  --secret="YOUR_SECRET_HERE" \
  --description="Personal AI assistant powered by CoPaw" \
  --state=enabled \
  --feature=bots-v1
```

**参数说明：**
- `--name`: Bot 显示名称
- `--url`: Webhook URL（你的 CoPaw 服务器地址）
- `--secret`: 共享密钥（用于消息签名验证）
- `--description`: Bot 描述
- `--state=enabled`: 启用 bot
- `--feature=bots-v1`: 启用基础 bot 功能

**可选参数：**
- `--feature=reaction`: 接收表情反应事件
- `--feature=events`: 使用事件系统（Nextcloud 31+）

命令执行后会返回 bot token，记录下来！

### 步骤 4：配置 CoPaw Channel

编辑 `~/.copaw/config.json`：

```json
{
  "channels": {
    "nextcloud_talk": {
      "enabled": true,
      "webhook_secret": "YOUR_SECRET_HERE",
      "webhook_host": "0.0.0.0",
      "webhook_port": 8765,
      "webhook_path": "/webhook/nextcloud_talk",
      "bot_prefix": "[BOT] "
    }
  }
}
```

**配置说明：**
- `enabled`: 启用 nextcloud_talk channel
- `webhook_secret`: 与 OCC 命令中相同的 secret
- `webhook_host`: Webhook 服务器监听地址（0.0.0.0 监听所有接口）
- `webhook_port`: Webhook 服务器端口
- `webhook_path`: Webhook 端点路径
- `bot_prefix`: Bot 消息前缀

### 步骤 5：配置反向代理（推荐）

如果使用 Nginx 作为 Nextcloud 反向代理，添加 webhook 路由配置：

```nginx
# 在 Nextcloud 的 server 块中添加
location = /webhook/nextcloud_talk {
    proxy_pass http://localhost:8765/webhook/nextcloud_talk;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # 设置后端 URL header（Nextcloud 需要）
    proxy_set_header X-Nextcloud-Talk-Backend https://$host;
}
```

**注意：** `X-Nextcloud-Talk-Backend` header 必须设置为你的 Nextcloud 实例的完整 URL。

### 步骤 6：重启 CoPaw

```bash
# 如果使用 systemd
sudo systemctl restart copaw

# 或者手动重启
copaw app start
```

### 步骤 7：在 Nextcloud Talk 中测试

1. 打开 Nextcloud Talk
2. 选择一个聊天（个人或群组）
3. 添加 Bot：
   - 点击聊天设置
   - 选择参与者
   - 搜索并添加你的 bot（显示名称：CoPaw Assistant）
4. 发送消息给 bot，应该会收到回复！

## 内网穿透方案（本地开发）

如果本地测试，可以使用 ngrok：

```bash
# 安装 ngrok
# macOS
brew install ngrok

# Linux
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
tar xvzf ngrok-v3-stable-linux-amd64.tgz
sudo mv ngrok /usr/local/bin

# 启动隧道
ngrok http 8765
```

ngrok 会提供公网 URL，例如：`https://abcd1234.ngrok.io`

配置 webhook URL：`https://abcd1234.ngrok.io/webhook/nextcloud_talk`

## 故障排查

### Bot 无法接收消息

1. 检查 CoPaw 日志：
   ```bash
   copaw logs | grep nextcloud_talk
   ```

2. 检查 webhook 端点是否可访问：
   ```bash
   curl https://your-server.com/webhook/nextcloud_talk
   ```

3. 检查 Nextcloud 服务端日志：
   ```bash
   tail -f /var/www/nextcloud/data/nextcloud.log
   ```

4. 验证签名：
   - 确认 secret 在 OCC 命令和 config.json 中完全一致
   - 检查 secret 是否包含特殊字符需要转义

### Bot 无法发送消息

1. 检查 bot token 是否正确保存
2. 确认 bot 已添加到目标聊天
3. 检查反向代理是否正确设置 `X-Nextcloud-Talk-Backend` header

### 权限问题

如果无法运行 OCC 命令，需要：
- 联系 Nextcloud 管理员执行 bot 安装
- 或者自己部署 Nextcloud 实例（完全控制）

## 安全建议

1. **使用 HTTPS**：Webhook URL 必须是 HTTPS，否则消息会被明文传输
2. **强密钥**：Secret 至少 32 字符，包含大小写字母、数字、符号
3. **验证签名**：CoPaw 会验证所有 webhook 请求的 HMAC-SHA256 签名
4. **限制访问**：可配置防火墙规则，只允许 Nextcloud 服务器 IP 访问 webhook 端点

## 高级配置

### 启用支持的表情反应

重新安装 bot，添加 `reaction` feature：

```bash
sudo -u www-data php occ talk:bot:install \
  --name="CoPaw Assistant" \
  --url="https://your-server.com/webhook/nextcloud_talk" \
  --secret="YOUR_SECRET_HERE" \
  --description="Personal AI assistant powered by CoPaw" \
  --state=enabled \
  --feature=bots-v1 \
  --feature=reaction
```

### 查看 Bot 信息

```bash
# 列出所有 bot
sudo -u www-data php occ talk:bot:list

# 查看特定 bot 详情
sudo -u www-data php occ talk:bot:info <TOKEN>
```

### 删除 Bot

```bash
sudo -u www-data php occ talk:bot:remove <TOKEN>
```

### 更新 Bot

Bot 更新时，先删除再重新安装（使用相同 token 可保持配置）：

```bash
# 删除
sudo -u www-data php occ talk:bot:remove <TOKEN>

# 重新安装（记住使用相同的 URL 和 secret）
sudo -u www-data php occ talk:bot:install \
  --name="CoPaw Assistant v2" \
  --url="https://your-server.com/webhook/nextcloud_talk" \
  --secret="YOUR_SECRET_HERE" \
  --description="Personal AI assistant powered by CoPaw" \
  --state=enabled \
  --feature=bots-v1
```

## API 参考

详细 API 文档：https://nextcloud-talk.readthedocs.io/en/latest/bots/

### 接收消息

Nextcloud Talk 通过 POST webhook 发送消息，格式为 Activity Streams 2.0。

### 发送消息

```bash
curl -X POST \
  "https://nextcloud.example.com/ocs/v2.php/apps/spreed/api/v1/bot/TOKEN/message" \
  -H "Content-Type: application/json" \
  -H "OCS-APIRequest: true" \
  -H "X-Nextcloud-Talk-Bot-Random: RANDOM" \
  -H "X-Nextcloud-Talk-Bot-Signature: SIGNATURE" \
  -d '{"message":"Hello from CoPaw!"}'
```

## 下一步

配置完成后，你可以：
1. 使用 Console 配置界面管理 channel（如果可用）
2. 配置 cron 定时任务，通过 Nextcloud Talk 发送定时提醒
3. 开发自定义功能，利用 Nextcloud Talk 的 reactions、文件分享等能力

需要帮助？查看 CoPaw 文档或提交 issue。

