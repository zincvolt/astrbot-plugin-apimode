# astrbot-plugin-apimode

本地模型 / 第三方 API 的切换与用量管理插件，用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)。

它做的是"运维层"的事：在不重启 QQ、不重新扫码的前提下，切换机器人背后的模型来源，控制 token 额度，并对指定群的输出做审查。

## 功能

- **模式切换**：`/切换 api 本地` 将模型切到本地 llama.cpp（8080），`/切换 api 第三方` 切到 OpenAI 兼容的第三方 API（如 DeepSeek）。切换由后台定时器落实，期间会停/起对应服务。
- **token 预算**：注入 LLM 请求前检查当日用量，超限则拦截；响应后累计 token 数。
- **省 token**：`/省token on|off` 控制是否压缩发给模型的上下文。
- **按群审查**：`/审查 on|off` 对指定群的模型输出做后置审查（仅第三方模式生效）。
- **用量查询**：`/用量` 看当日已用 token。
- **判官开关**：`/判官 状态` 看第三方判官的开关、条数前处理、概率闸、话段聚合与今日 tokens；`/判官 开关 on|off`、`/判官 预算 on|off`、`/判官 概率 on|off`、`/判官 话段 on|off`、`/判官 等待 N`、`/判官 限额 N` 改配置（写 `data/judge_api.json`，无需重启）。

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

## 判官配置（`data/judge_api.json`）

`/判官` 命令改的是另一份文件，和上面这份记账文件无关。完整字段见
[`judge_api.example.json`](judge_api.example.json)，核心几项：

| 字段 | 默认 | 作用 |
|---|---|---|
| `enable` | true | 第三方判官总开关；关掉退回「规则 + 概率」 |
| `budget_gate` | true | 条数限制前处理，超预算不调模型 |
| `use_reply_chance` / `reply_chance` | false / 1.0 | 判官说接之后是否再抽一次概率 |
| `fallback_chance` | 0.3 | 判官失败/超限时的兜底概率 |
| `burst_enable` | true | 话段聚合总开关 |
| `burst_settle` | 3.5 | 判定前等这一口气说完的秒数 |
| `burst_gap` | 15 | 两条消息间隔小于它算同一段 |
| `burst_reuse` | 45 | 这一段判过/回过后不再送判官的秒数（省 token） |
| `reply_gap` | 6 | 回复真正发完后的静默秒数 |
| `inflight_ttl` | 45 | 判定通过后「回复在途」最长认领秒数 |
| `bot_names` | `["阿灰"]` | 叫到这些名字直接接，不吃冷却 |
| `break_words` | 见示例 | 脏话词表，命中只代表「让判官看一眼」，由判官判断在骂谁 |

- `api_key` 留空表示沿用主模型（deepseek provider）的密钥
- 判官的判定链路与提示词在主仓 AstrBot 补丁 `group_chat_context.py` 里，本插件只负责开关、记账和这几条命令
- 本地模式（`local-llama`）不读这份文件，仍用 `data/judge.json` + 8082；话段聚合也只在第三方模式生效

### 关于「发送耗时」

回复是 AstrBot 分段发出去的（`platform_settings.segmented_reply`），每段之间会
`sleep`。所以「模型生成完」不等于「消息发完」，判官补丁会按分段配置估算真实
发送时长（human 模式 ≈ 每段 `0.45 + 字数/7`，乘 0.75~1.35 抖动，单段上限 5.5s），
把冷却期挪到「预计发完」之后。要复用这套逻辑，直接抄
`_estimate_send_seconds()` / `_split_segments()` 两个函数即可，它们不依赖 AstrBot 运行时。

## 依赖

- AstrBot 4.28+
- 本地模式下需要本机 8080 端口的 llama.cpp 兼容服务

## 许可证

AGPL-3.0-or-later，见 [LICENSE](LICENSE)。
