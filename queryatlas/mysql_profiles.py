"""Server-managed MySQL credentials exposed to the console as workspace-scoped references."""

import os
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .mysql_source import MysqlConnection, SyncRejected


class Profile(MysqlConnection):
    space: str
    label: str


def profiles(space):
    path = Path(os.getenv("QA_MYSQL_PROFILES", "/etc/queryatlas/mysql/profiles.json"))
    try:
        records = TypeAdapter(dict[str, Profile]).validate_json(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, ValidationError):
        raise SyncRejected("服务器上的 MySQL 连接配置无法读取，请联系平台管理员") from None
    return {name: profile for name, profile in records.items() if profile.space == space}


def get_profile(space, name):
    profile = profiles(space).get(name)
    if not profile:
        raise SyncRejected("连接配置已移除或不属于当前工作空间")
    return profile
