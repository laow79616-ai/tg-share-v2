"""
配置管理模块
- 管理 API 配置、Worker 配置、用户名列表、发送日志、IP池
"""
import os
import json
import logging
from pathlib import Path

logger = logging.getLogger("Config")

# 基础路径
BASE_DIR = Path("/root/tg_share_v2")
DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
LOGS_DIR = BASE_DIR / "logs"

# 配置文件
BOT_CONFIG_FILE = DATA_DIR / "bot_config.json"
WORKERS_CONFIG_FILE = DATA_DIR / "workers_config.json"
TARGETS_FILE = DATA_DIR / "targets.json"
BOTS_FILE = DATA_DIR / "bots.json"
PROXY_POOL_FILE = DATA_DIR / "proxy_pool.json"
AD_CONFIG_FILE = DATA_DIR / "ad_config.json"
SCHEDULE_FILE = DATA_DIR / "schedule.json"
STATS_FILE = DATA_DIR / "stats.json"

# 确保目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def load_json(filepath):
    """安全加载JSON文件"""
    filepath = Path(filepath)
    if filepath.exists():
        try:
            return json.loads(filepath.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载文件失败 {filepath}: {e}")
    return {}


def save_json(filepath, data):
    """安全保存JSON文件"""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
