"""Lora 的 Context 别名——直接复用 SDK 的 ``PygentContext``。

类型身份必须与 SDK 一致：pygent_v1 的 ``ContextCodecRegistry`` 按精确类型
查找 codec，本地副本会产生未注册类型。数据根与磁盘布局不受影响。
"""

from pygent.agent.pygent_v1.runtime.context import (
    PYGENT_CONTEXT_CODEC as LARA_CONTEXT_CODEC,
    PYGENT_CONTEXT_CODECS as LARA_CONTEXT_CODECS,
    PygentContext as LaraContext,
)

__all__ = ["LARA_CONTEXT_CODECS", "LaraContext"]
