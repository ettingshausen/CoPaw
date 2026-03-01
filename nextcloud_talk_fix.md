# Nextcloud Talk 集成修复说明

## 问题描述

在处理 Nextcloud Talk webhook 请求时出现以下错误：

```
AttributeError: 'list' object has no attribute 'keys'
```

错误发生在 `src/copaw/app/channels/nextcloud_talk/content_utils.py` 第93行：
```python
f"params_keys={list(parameters.keys())}"
```

## 根本原因

当 Nextcloud Talk 发送的消息内容中 `parameters` 字段是列表类型（而非预期的字典类型）时，代码尝试调用 `.keys()` 方法会导致 AttributeError。

这种情况可能出现在：
1. 特定版本的 Nextcloud Talk 发送的数据格式
2. 某些特殊消息类型的内容结构
3. 数据解析过程中的异常情况

## 修复方案

对 `content_utils.py` 文件进行了以下修改：

### 1. 添加缺失的常量导入
```python
from .constants import (
    # ... 其他导入
    SESSION_ID_SUFFIX_LEN,  # 新增导入
)
```

### 2. 修改 `parse_message_content` 方法
```python
# 原代码
parameters = content_data.get("parameters", {})

# 修复后
parameters = content_data.get("parameters", {})
# 确保 parameters 是字典类型
if not isinstance(parameters, dict):
    logger.debug(f"parameters is not dict, converting from {type(parameters)}")
    parameters = {}
```

### 3. 修改日志输出
```python
# 原代码
f"params_keys={list(parameters.keys())}"

# 修复后
f"params_type={type(parameters).__name__} params_keys={list(parameters.keys()) if isinstance(parameters, dict) else 'N/A'}"
```

### 4. 修改 `replace_mentions` 方法
```python
# 新增安全检查
if not isinstance(parameters, dict):
    logger.debug(f"replace_mentions: parameters is not dict, type={type(parameters)}")
    return text
```

## 测试验证

创建了测试用例验证修复效果：
- ✅ parameters 为列表时正常处理
- ✅ parameters 为字典时正常处理  
- ✅ 无效 JSON 时正常处理

## 影响范围

此修复仅影响 Nextcloud Talk 频道的消息处理逻辑，不会影响其他功能模块。

### 5. 修复 429 速率限制问题

Nextcloud Talk Bot API 有速率限制，Agent 的多个中间消息会触发 `429 Reached maximum delay` 错误。

**解决方案**：重写 `_run_process_loop` 方法，收集所有消息并在最后合并为一条消息发送：

```python
async def _run_process_loop(
    self,
    request: "AgentRequest",
    to_handle: str,
    send_meta: Dict[str, Any],
) -> None:
    """Override to batch messages for rate limiting."""
    from agentscope_runtime.engine.schemas.agent_schemas import RunStatus
    
    session_id = getattr(request, "session_id", "") or ""
    bot_prefix = send_meta.get("bot_prefix", "") or ""
    
    # Collect all messages
    all_messages: List[str] = []
    last_response = None
    
    try:
        async for event in self._process(request):
            obj = getattr(event, "object", None)
            status = getattr(event, "status", None)
            
            if obj == "message" and status == RunStatus.Completed:
                text = self._extract_text_from_event(event)
                if text:
                    all_messages.append(text)
                    
            elif obj == "response":
                last_response = event
                await self.on_event_response(request, event)
        
        # Send all collected messages as a single message
        if all_messages:
            final_message = "\n\n".join(all_messages)
            if bot_prefix:
                final_message = bot_prefix + final_message
            await self.send(to_handle, final_message, send_meta)
        
        # Handle errors and callbacks...
```

## 测试验证

创建了测试用例验证修复效果：
- ✅ parameters 为列表时正常处理
- ✅ parameters 为字典时正常处理  
- ✅ 无效 JSON 时正常处理
- ✅ Secret 验证通过（不再 401）
- ✅ 消息合并发送（避免 429）

## 影响范围

此修复仅影响 Nextcloud Talk 频道的消息处理逻辑，不会影响其他功能模块。

## 部署建议

1. 更新 `content_utils.py` 和 `channel.py` 文件
2. 重启 CoPaw 服务
3. 监控 Nextcloud Talk 集成的日志输出确认问题已解决