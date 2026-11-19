"""Switch between the local model and a third-party API, with budgets and review.

State lives in ``data/api_mode.json``:

    {
      "mode": "local" | "third_party",
      "save_token": true,
      "daily_limit": 200000,
      "used": {"date": "20261118", "tokens": 0},
      "review_groups": {"942316942": true}
    }

Admin commands (slash):
    /切换 api 本地 | /切换 api 第三方
    /额度 200000
    /省token on|off
    /审查 on|off          (per group, only active in third_party mode)
    /用量
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

ROOT = Path(r"D:\QQNTBOT")
STATE = ROOT / "data" / "api_mode.json"
CONFIG = ROOT / "apps" / "AstrBot" / "data" / "cmd_config.json"
REPLY = ROOT / "data" / "reply.json"
WORDS = ROOT / "data" / "review_words.txt"
ADMINS = {"1154487120"}

DEFAULT_WORDS = [
    "习近平", "共产党", "台独", "六四", "天安门事件", "法轮功",
    "裸照", "约炮", "开房", "包养", "幼女", "未成年做爱",
    "炸弹制作", "制毒", "冰毒配方",
]


def load_state() -> dict:
    """Return the persisted API mode state."""
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"mode": "local", "save_token": True, "daily_limit": 200000,
            "used": {"date": "", "tokens": 0}, "review_groups": {}}


def save_state(state: dict) -> None:
    """Persist the API mode state."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def today() -> str:
    """Return today's date key."""
    return datetime.now().strftime("%Y%m%d")


def review_words() -> list[str]:
    """Load the review word list, creating the default file when missing."""
    if not WORDS.exists():
        WORDS.write_text("\n".join(DEFAULT_WORDS) + "\n", encoding="utf-8")
    try:
        raw = WORDS.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_WORDS
    return [w.strip() for w in raw.splitlines() if w.strip() and not w.startswith("#")]


LOCAL_PORTS = (8080, 8083, 8085)   # 聊天 / OCR / 识图；判官 8082 与嵌入 8097 保留
LOCAL_SCRIPTS = {
    8080: r"D:\LLM\scripts\start-router.ps1",
    8083: r"D:\QQNTBOT\scripts\start-ocr.ps1",
    8085: r"D:\LLM\scripts\start-vl.ps1",
}


def _port_pids(port: int) -> list[int]:
    """Return PIDs listening on a local port.

    Args:
        port: TCP port to inspect.

    Returns:
        List of process ids.
    """
    import psutil

    pids: list[int] = []
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
            if conn.pid and conn.pid not in pids:
                pids.append(conn.pid)
    return pids


VISION_STATE = ROOT / "data" / "vision_mode.json"


def set_vision_mode(mode: str) -> str:
    """Point the vision backend at local or third-party to match the API mode.

    Args:
        mode: ``local`` or ``third_party``.

    Returns:
        The previous vision mode for logging.
    """
    try:
        state = json.loads(VISION_STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        state = {}
    previous = state.get("mode", "local")
    state["mode"] = mode
    state.setdefault("port", 8085)
    state.setdefault("model", "Qwen3VL-4B-Instruct")
    VISION_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return previous


def unload_local_models() -> list[str]:
    """Stop the local chat, OCR and vision servers.

    Returns:
        Names of the ports that were shut down.
    """
    import psutil

    stopped: list[str] = []
    for port in LOCAL_PORTS:
        for pid in _port_pids(port):
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                stopped.append(f"{port}({proc.name()})")
            except Exception:  # noqa: BLE001
                continue
    logger.info(f"[apimode] unloaded local models: {stopped or 'none'}")
    return stopped


def start_local_models() -> None:
    """Start the local chat, OCR and vision servers again (detached)."""
    import subprocess

    for port, script in LOCAL_SCRIPTS.items():
        if _port_pids(port):
            continue
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    logger.info("[apimode] requested local model startup")


def set_provider_mode(mode: str) -> str:
    """Rewrite AstrBot provider enable flags for the requested mode.

    Args:
        mode: ``local`` or ``third_party``.

    Returns:
        A short human-readable result message.
    """
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    providers = cfg.get("provider") or []
    local_ids = {"local-llama", "vl-ocr"}
    changed = []
    for p in providers:
        pid = p.get("id", "")
        if pid in local_ids:
            p["enable"] = mode == "local"
            changed.append(f"{pid}={p['enable']}")
        elif pid in ("deepseek", "deepseek-api"):
            p["enable"] = mode == "third_party"
            changed.append(f"{pid}={p['enable']}")

    # 真正决定用哪个 provider 的地方是 agent_runner，必须一起改
    runner = cfg.setdefault("agent_runner", {})
    runner["runner_type"] = "local"
    model_cfg = runner.setdefault("config", {}).setdefault("model", {})
    if mode == "third_party":
        model_cfg["provider_id"] = "deepseek"
        model_cfg["fallback_provider_ids"] = ["local-llama"]
    else:
        model_cfg["provider_id"] = "local-llama"
        model_cfg["fallback_provider_ids"] = ["deepseek"]

    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return (
        " ".join(changed) + f" | 主模型={model_cfg['provider_id']}"
    ) or "没有找到可切换的 provider"


DEFAULT_RULE = {"pattern": "。(?=\\s|$|[（(\\[【「『])", "to": "~"}


def tune_reply_for_mode(mode: str) -> None:
    """Turn local-only heavy features on or off to match the mode.

    Third-party mode strips the local crutches (sticker probability, regex
    rewriting, dual judge); switching back restores them so the local 9B keeps
    its usual behaviour. Prompts are never touched here.
    """
    try:
        data = json.loads(REPLY.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if mode == "third_party":
        data["dual_judge"] = False
        data["thinking"] = {"enable": False, "budget": 0}
        data["meme_chance"] = 0
        data["text_rules"] = []
    else:
        data["dual_judge"] = True
        data["thinking"] = {"enable": False, "budget": 0}
        if not float(data.get("meme_chance", 0) or 0):
            data["meme_chance"] = 0.3
        if not (data.get("text_rules") or []):
            data["text_rules"] = [dict(DEFAULT_RULE)]
    REPLY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def tune_local_provider() -> None:
    """Reset the local provider body to sane non-thinking defaults."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    for provider in cfg.get("provider", []):
        if provider.get("id") != "local-llama":
            continue
        body = provider.setdefault("custom_extra_body", {})
        body["max_tokens"] = min(int(body.get("max_tokens", 200) or 200), 300)
        body.setdefault("temperature", 0.5)
        body.setdefault("top_p", 0.9)
        body.setdefault("repeat_penalty", 1.06)
        body["chat_template_kwargs"] = {"enable_thinking": False}
    runner = cfg.setdefault("agent_runner", {}).setdefault("config", {}).setdefault(
        "model", {}
    )
    runner["request_max_retries"] = int(runner.get("request_max_retries", 5) or 5)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def schedule_restart(delay: int = 8) -> None:
    """Restart AstrBot in the background so config changes take effect."""
    script = ROOT / "scripts" / "restart-astrbot.ps1"
    cmd = (
        f"Start-Sleep -Seconds {delay}; "
        f"& '{script}'"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def set_thinking(mode: str) -> str:
    """Configure DeepSeek thinking mode on the provider entry.

    Args:
        mode: ``off``, ``on``, or an effort level (``low``/``high``/``max``).

    Returns:
        A short description of what was applied.
    """
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    for provider in cfg.get("provider", []):
        if provider.get("id") != "deepseek":
            continue
        body = provider.setdefault("custom_extra_body", {})
        if mode == "off":
            body["thinking"] = {"type": "disabled"}
            body.pop("reasoning_effort", None)
            body["temperature"] = 0.8
            note = "思维链关闭"
        else:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "high" if mode == "on" else mode
            # 思考模式不支持 temperature，留着也会被忽略
            body.pop("temperature", None)
            note = f"思维链开启，强度 {body['reasoning_effort']}"
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return note
    return "没找到 deepseek provider"


class Main(star.Star):
    """Admin switch for API mode, token budget, and per-group review."""

    def __init__(self, context: star.Context) -> None:
        self.context = context
        logger.info("[apimode] ready")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        return str(event.get_sender_id()) in ADMINS

    @filter.on_llm_request()
    async def budget_gate(self, event: AstrMessageEvent, req) -> None:
        """Block requests once the daily token budget is used up."""
        state = load_state()
        if state.get("mode") != "third_party":
            return
        if state.get("save_token") and getattr(req, "contexts", None):
            # 省 token：保留条数由控制台滑块决定
            keep = int(state.get("history_keep", 10) or 10)
            if len(req.contexts) > keep:
                req.contexts = req.contexts[-keep:]
        used = state.get("used") or {}
        if used.get("date") != today():
            return
        limit = int(state.get("daily_limit") or 0)
        if limit and int(used.get("tokens") or 0) >= limit:
            await event.send(event.plain_result("今日第三方额度已用完，先不聊了"))
            event.stop_event()
            logger.warning("[apimode] daily budget exhausted")

    @filter.on_llm_response()
    async def count_usage(self, event: AstrMessageEvent, resp) -> None:
        """Accumulate token usage reported by the provider."""
        usage = getattr(resp, "usage", None)
        tokens = 0
        if usage is not None:
            for attr in ("total_tokens", "total_token_count"):
                value = getattr(usage, attr, None)
                if isinstance(value, int):
                    tokens = value
                    break
            if not tokens:
                for attr in ("input_tokens", "prompt_tokens"):
                    tokens += int(getattr(usage, attr, 0) or 0)
                for attr in ("output_tokens", "completion_tokens"):
                    tokens += int(getattr(usage, attr, 0) or 0)
        if not tokens:
            return
        state = load_state()
        used = state.get("used") or {}
        if used.get("date") != today():
            used = {"date": today(), "tokens": 0}
        used["tokens"] = int(used.get("tokens") or 0) + int(tokens)
        state["used"] = used
        save_state(state)

    @filter.on_decorating_result()
    async def review_output(self, event: AstrMessageEvent) -> None:
        """Filter replies in reviewed groups while on the third-party API."""
        state = load_state()
        if state.get("mode") != "third_party":
            return
        group = str(event.get_group_id() or "")
        if not group or not (state.get("review_groups") or {}).get(group):
            return
        result = event.get_result()
        if not result or not getattr(result, "chain", None):
            return
        words = review_words()
        hit = ""
        for comp in result.chain:
            text = getattr(comp, "text", None)
            if not isinstance(text, str):
                continue
            for word in words:
                if word in text:
                    hit = word
                    break
            if hit:
                break
        if not hit:
            return
        logger.warning(f"[apimode] review blocked group={group} word={hit}")
        for comp in result.chain:
            if getattr(comp, "text", None):
                comp.text = "这个话题我不接"

    def _current_provider_id(self) -> str:
        """Return the chat provider id configured in agent_runner.

        Returns:
            Provider id string, or an empty string when unreadable.
        """
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
            return str(
                cfg.get("agent_runner", {})
                .get("config", {})
                .get("model", {})
                .get("provider_id", "")
            )
        except (json.JSONDecodeError, OSError):
            return ""

    @filter.on_astrbot_loaded()
    async def watch_provider_switch(self) -> None:
        """Start the provider watcher."""
        import asyncio

        asyncio.create_task(self._provider_watch())

    async def _provider_watch(self) -> None:
        """Poll the active provider and react to local <-> API transitions."""
        import asyncio

        last = self._current_provider_id()
        while True:
            await asyncio.sleep(20)
            try:
                current = self._current_provider_id()
                if current == last:
                    continue
                was_api = "deepseek" in last
                is_api = "deepseek" in current
                if is_api and not was_api:
                    set_vision_mode("third_party")
                    stopped = unload_local_models()
                    logger.info(f"[apimode] provider->API, unloaded {stopped}")
                elif was_api and not is_api:
                    set_vision_mode("local")
                    start_local_models()
                    logger.info("[apimode] provider->local, starting local models")
                last = current
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[apimode] provider watch failed: {exc}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle(self, event: AstrMessageEvent) -> None:
        """Handle the admin commands."""
        text = (event.message_str or "").strip()
        if not text:
            return
        raw = ""
        msg_obj = getattr(event, "message_obj", None)
        raw_msg = getattr(msg_obj, "raw_message", None)
        if isinstance(raw_msg, dict):
            raw = str(raw_msg.get("raw_message") or "")
        slash = raw.lstrip().startswith(("/", "／")) or text.startswith(("/", "／"))
        if not slash:
            return
        body = re.sub(r"^\s*[/／]\s*", "", text).strip()

        state = load_state()

        if body in ("用量", "额度查询"):
            used = state.get("used") or {}
            await event.send(event.plain_result(
                f"模式 {state.get('mode')} · 今日 {used.get('tokens', 0)} / "
                f"{state.get('daily_limit')} tokens · 省token "
                f"{'开' if state.get('save_token') else '关'}"
            ))
            event.stop_event()
            return

        if not self._is_admin(event):
            if body.startswith(("切换", "额度", "省token", "审查", "判官")):
                await event.send(event.plain_result("只有管理员能改这些"))
                event.stop_event()
            return

        if body.startswith("判官"):
            path = ROOT / "data" / "judge_api.json"
            try:
                judge_data = (
                    json.loads(path.read_text(encoding="utf-8-sig"))
                    if path.exists()
                    else {}
                )
            except (json.JSONDecodeError, OSError):
                judge_data = {}
            arg = body.replace("判官", "", 1).strip()
            low = arg.lower()
            if low in ("", "状态"):
                used = judge_data.get("used") or {}
                await event.send(event.plain_result(
                    f"判官 {'开' if judge_data.get('enable', True) else '关'} · "
                    f"条数前处理 {'开' if judge_data.get('budget_gate', True) else '关'} · "
                    f"概率闸 {'开' if judge_data.get('use_reply_chance') else '关'} · "
                    f"今日 {used.get('tokens', 0)}/"
                    f"{judge_data.get('daily_limit', 0)} tokens"
                ))
                event.stop_event()
                return
            if low.startswith(("预算", "条数")):
                value = low.endswith(("on", "开"))
                judge_data["budget_gate"] = value
                note = f"条数前处理 {'开' if value else '关'}"
            elif low.startswith(("概率", "chance")):
                value = low.endswith(("on", "开"))
                judge_data["use_reply_chance"] = value
                note = f"概率闸 {'开' if value else '关'}"
            elif low.startswith(("开关", "enable")):
                value = low.endswith(("on", "开"))
                judge_data["enable"] = value
                note = f"第三方判官 {'开' if value else '关'}"
            elif low.startswith(("限额", "limit")):
                numbers = re.findall(r"\d+", arg)
                if not numbers:
                    await event.send(event.plain_result("用法：/判官 限额 50000"))
                    event.stop_event()
                    return
                judge_data["daily_limit"] = int(numbers[0])
                note = f"日限额 {judge_data['daily_limit']} tokens"
            else:
                await event.send(event.plain_result(
                    "用法：/判官 [状态|开关 on/off|预算 on/off|概率 on/off|限额 N]"
                ))
                event.stop_event()
                return
            path.write_text(
                json.dumps(judge_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await event.send(event.plain_result(f"已保存：{note}"))
            event.stop_event()
            return

        if body.startswith("切换"):
            if "第三方" in body or "api" in body.lower():
                state["mode"] = "third_party"
                set_provider_mode("third_party")
                tune_reply_for_mode("third_party")
                set_thinking("off")
                set_vision_mode("third_party")
                stopped = unload_local_models()
                save_state(state)
                detail = "、".join(stopped) if stopped else "无运行中的本地模型"
                await event.send(event.plain_result(
                    f"已切到第三方 API，8 秒后重启生效；已卸载本地模型：{detail}"
                ))
                schedule_restart()
            else:
                state["mode"] = "local"
                set_provider_mode("local")
                tune_reply_for_mode("local")
                tune_local_provider()
                start_local_models()
                save_state(state)
                await event.send(event.plain_result(
                    "已切回本地模型，8 秒后重启生效"
                ))
                schedule_restart()
            event.stop_event()
            return

        if body.startswith("思维链"):
            level = body.replace("思维链", "").strip().lower()
            if level in ("", "on", "开"):
                level = "on"
            elif level in ("off", "关"):
                level = "off"
            elif level not in ("low", "high", "max"):
                level = "high"
            note = set_thinking(level)
            await event.send(event.plain_result(f"{note}（第三方模式生效，8 秒后重启）"))
            schedule_restart()
            event.stop_event()
            return

        if body.startswith("额度"):
            m = re.search(r"(\d+)", body)
            if m:
                state["daily_limit"] = int(m.group(1))
                save_state(state)
                await event.send(event.plain_result(f"每日额度设为 {m.group(1)} tokens"))
            event.stop_event()
            return

        if body.lower().startswith("省token"):
            on = "on" in body.lower() or "开" in body
            state["save_token"] = on
            save_state(state)
            await event.send(event.plain_result(f"省token模式 {'开' if on else '关'}"))
            event.stop_event()
            return

        if body.startswith("审查"):
            group = str(event.get_group_id() or "")
            if not group:
                await event.send(event.plain_result("请在群里发这条"))
                event.stop_event()
                return
            groups = state.get("review_groups") or {}
            on = "on" in body.lower() or "开" in body
            groups[group] = on
            state["review_groups"] = groups
            save_state(state)
            note = "" if state.get("mode") == "third_party" else "（当前不是第三方模式，暂不生效）"
            await event.send(event.plain_result(
                f"本群审查{'开' if on else '关'}{note}"
            ))
            event.stop_event()
