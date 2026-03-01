# Nextcloud Talk Console UI Support

> After pushing changes, the console UI needs to be rebuilt and deployed.

## Changes Made

### 1. Added Nextcloud Talk to Channel Types (`console/src/api/types/channel.ts`)
```typescript
export interface NextcloudTalkConfig extends BaseChannelConfig {
  webhook_secret: string;      // WebSocket signing secret
  webhook_host: string;        // Listen address (0.0.0.0)
  webhook_port: number;        // Listen port (8765)
  webhook_path: string;        // Webhook path (/webhook/nextcloud_talk)
}
```

### 2. Updated Type Definitions
```typescript
export interface ChannelConfig {
  // ... other channels
  nextcloud_talk: NextcloudTalkConfig;  // <- Added
  console: ConsoleConfig;
}

export type SingleChannelConfig =
  | // ... other channels
  | NextcloudTalkConfig  // <- Added
  | ConsoleConfig;
```

### 3. Added Display Label (`console/src/pages/Control/Channels/components/constants.ts`)
```typescript
export const CHANNEL_LABELS: Record<ChannelKey, string> = {
  // ... other channels
  nextcloud_talk: "Nextcloud Talk",  // <- Added
  console: "Console",
};
```

### 4. Added Configuration Form (`console/src/pages/Control/Channels/components/ChannelDrawer.tsx`)
```typescript
case "nextcloud_talk":
  return (
    <>
      <Form.Item name="webhook_secret" label="Webhook Secret" rules={[{ required: true }]}>
        <Input.Password placeholder="Generate with: openssl rand -hex 32" />
      </Form.Item>
      <Form.Item name="webhook_host" label="Webhook Host">
        <Input placeholder="0.0.0.0" />
      </Form.Item>
      <Form.Item name="webhook_port" label="Webhook Port" rules={[{ required: true }]}>
        <InputNumber min={1} max={65535} placeholder="8765" />
      </Form.Item>
      <Form.Item name="webhook_path" label="Webhook Path">
        <Input placeholder="/webhook/nextcloud_talk" />
      </Form.Item>
    </>
  );
```

## Required Actions

### Option 1: Build Console Locally
```bash
cd console
npm install
npm run build
# Copy dist/ to your web server location
```

### Option 2: Use Docker/Deploy Script
```bash
# Check deploy/ directory for deployment script
cd deploy
# Run build command
```

### Option 3: Development Mode
```bash
cd console
npm run dev
# Access at http://192.168.31.138:5173/channels
```

## Configuration Steps (After React Build)

1. Open http://192.168.31.138:8088/channels
2. Find "Nextcloud Talk" card
3. Click settings (gear icon)
4. Configure:
   - **Enabled**: ON
   - **Bot Prefix**: `[BOT] ` (or your preference)
   - **Webhook Secret**: Generate with `openssl rand -hex 32`
   - **Webhook Host**: `0.0.0.0`
   - **Webhook Port**: `8765` (or your preference)
   - **Webhook Path**: `/webhook/nextcloud_talk`
5. Save

## Important Notes

- The `webhook_secret` must match the secret used when installing the Nextcloud bot
- Ensure the webhook port (8765) is open in your firewall
- For production, configure a reverse proxy (Nginx) to forward `/webhook/nextcloud_talk`
- After saving, you may need to restart CoPaw for the changes to take effect

## Troubleshooting

### Channel not showing after reload
- Clear browser cache (Ctrl+F5)
- Check console errors (F12 -> Console)
- Verify the build was successful

### Configuration not saving
- Check if CoPaw backend is running
- Check API endpoints in Network tab (F12 -> Network)
- Verify config file permissions

### Webhook not receiving messages
- Check port 8765 is open: `netstat -an | grep 8765`
- Check CoPaw logs: `copaw logs | grep nextcloud_talk`
- Test webhook: `curl -X POST http://192.168.31.138:8765/webhook/nextcloud_talk`
