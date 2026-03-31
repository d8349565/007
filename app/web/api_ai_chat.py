"""AI 数据助手 — 路由 Blueprint"""

import json
import os
import yaml

from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context

from app.config import get_config, PROJECT_ROOT
from app.logger import get_logger

logger = get_logger(__name__)

ai_chat_bp = Blueprint("ai_chat", __name__, url_prefix="/ai")


@ai_chat_bp.route("/")
def ai_page():
    """AI 助手聊天页面"""
    from app.services.ai_chat import get_current_settings
    settings = get_current_settings()
    return render_template("ai_chat.html", active="ai", settings=settings)


@ai_chat_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """处理对话请求，返回 JSON 结果"""
    from app.services.ai_chat import chat

    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])
    model = data.get("model", "")
    temperature = data.get("temperature", 0.3)

    if not messages:
        return jsonify({"error": "messages 不能为空"}), 400

    # 限制消息数量防止过大请求
    messages = messages[-20:]

    try:
        result = chat(messages, model_override=model, temperature=temperature)
        return jsonify(result)
    except Exception as e:
        logger.error("AI 对话失败: %s", e, exc_info=True)
        return jsonify({"error": f"对话失败: {str(e)}"}), 500


@ai_chat_bp.route("/api/settings", methods=["GET"])
def api_get_settings():
    """获取当前设置"""
    from app.services.ai_chat import get_current_settings
    return jsonify(get_current_settings())


@ai_chat_bp.route("/api/settings", methods=["POST"])
def api_save_settings():
    """保存设置到 config.yaml"""
    data = request.get_json(silent=True) or {}

    config_path = PROJECT_ROOT / "config.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        # 更新 LLM 设置
        if "provider" in data:
            cfg["llm"]["provider"] = data["provider"]
        if "temperature" in data:
            cfg["llm"]["temperature"] = float(data["temperature"])
        if "max_tokens" in data:
            cfg["llm"]["max_tokens"] = int(data["max_tokens"])
        if "timeout" in data:
            cfg["llm"]["timeout"] = int(data["timeout"])
        if "max_retries" in data:
            cfg["llm"]["max_retries"] = int(data["max_retries"])

        # 如果切换了 provider 的 model
        provider = data.get("provider", cfg["llm"].get("provider", "deepseek"))
        if "model" in data and provider in cfg["llm"]:
            cfg["llm"][provider]["model"] = data["model"]

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        # 重新加载配置单例
        import app.config as config_mod
        config_mod._config = None

        # 重置 LLM 客户端单例
        import app.services.llm_client as llm_mod
        llm_mod._client = None

        logger.info("AI 设置已保存: provider=%s", provider)
        return jsonify({"ok": True})

    except Exception as e:
        logger.error("保存设置失败: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@ai_chat_bp.route("/api/env", methods=["GET"])
def api_get_env():
    """获取环境变量状态（不返回实际值）"""
    env_keys = ["DEEPSEEK_API_KEY", "KIMI_API_KEY", "MINIMAX_API_KEY"]
    result = {}
    for key in env_keys:
        val = os.getenv(key, "")
        result[key] = {
            "set": bool(val),
            "preview": val[:4] + "****" + val[-4:] if len(val) > 8 else ("****" if val else ""),
        }
    return jsonify(result)


@ai_chat_bp.route("/api/env", methods=["POST"])
def api_save_env():
    """保存环境变量到 .env 文件"""
    data = request.get_json(silent=True) or {}

    env_path = PROJECT_ROOT / ".env"
    try:
        # 读取现有 .env
        existing = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()

        # 更新
        allowed_keys = {"DEEPSEEK_API_KEY", "KIMI_API_KEY", "MINIMAX_API_KEY"}
        for key, value in data.items():
            if key in allowed_keys and value:
                existing[key] = value
                os.environ[key] = value  # 同时更新运行时环境

        # 写回
        lines = [f"{k}={v}" for k, v in existing.items()]
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 重置配置和客户端
        import app.config as config_mod
        config_mod._config = None
        import app.services.llm_client as llm_mod
        llm_mod._client = None

        logger.info("环境变量已更新")
        return jsonify({"ok": True})

    except Exception as e:
        logger.error("保存环境变量失败: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@ai_chat_bp.route("/api/test-connection", methods=["POST"])
def api_test_connection():
    """测试 LLM 连接是否正常"""
    from app.services.llm_client import LLMClient

    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "")

    try:
        cfg = get_config()["llm"]
        if provider and provider in cfg:
            # 临时创建指定 provider 的客户端
            temp_cfg = dict(cfg)
            temp_cfg["provider"] = provider
            pcfg = cfg[provider]
            api_key = os.getenv(pcfg.get("api_key_env", ""), pcfg.get("api_key", ""))
            if not api_key:
                return jsonify({"ok": False, "error": f"未设置 {pcfg.get('api_key_env', '')} 环境变量"})
            temp_cfg["api_key"] = api_key
            temp_cfg["base_url"] = pcfg.get("base_url", "")
            temp_cfg["model"] = pcfg.get("model", "")
            client = LLMClient(temp_cfg)
        else:
            client = LLMClient()

        result = client.chat("你是测试助手", "请回复'连接正常'四个字", max_tokens=20)
        return jsonify({
            "ok": True,
            "model": result.get("model", ""),
            "reply": result.get("content", "")[:50],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]})
