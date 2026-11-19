# astrbot-plugin-apimode

本地模型 / 第三方 API 的切换与用量管理插件，用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)。

它做的是"运维层"的事：在不重启 QQ、不重新扫码的前提下，切换机器人背后的模型来源，控制 token 额度，并对指定群的输出做审查。

## 功能

- **模式切换**：`/切换 api 本地` 将模型切到本地 llama.cpp（8080），`/切换 api 第三方` 切到 OpenAI 兼容的第三方 API（如 DeepSeek）。切换由后台定时器落实，期间会停/起对应服务。
- **token 预算**：注入 LLM 请求前检查当日用量，超限则拦截；响应后累计 token 数。
- **省 token**：`/省token on|off` 控制是否压缩发给模型的上下文。
- **按群审查**：`/审查 on|off` 对指定群的模型输出做后置审查（仅第三方模式生效）。
- **用量查询**：`/用量` 看当日已用 token。

以上管理命令仅限管理员。

## 状态文件

状态写在 AstrBot 数据目录下的 `data/api_mode.json`：

```json
{
  "mode": "local | third_party",
  "save_token": true,
  "daily_limit": 200000,
  "used": { "date": "20261118", "tokens": 0 },
  "review_groups": { "942316942": true }
}
```

> 注意：本插件的状态文件只是记账。**模式的唯一真相是 AstrBot 自身的 `provider_id` 配置**，本插件切换时同步修改它；手动改 `api_mode.json` 不会真正生效。

## 依赖

- AstrBot 4.28+
- 本地模式下需要本机 8080 端口的 llama.cpp 兼容服务

## 许可证

AGPL-3.0-or-later，见 [LICENSE](LICENSE)。
