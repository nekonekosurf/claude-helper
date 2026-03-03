"""
coding_agent - vLLM ベースのローカルコーディングエージェント

Claude Code と同等のコーディングエージェントをローカル LLM で実装する。
"""

from .agent_core import AgentCore, AgentResponse, AgentMode
from .config import Config, LLMConfig, AgentConfig, get_config
from .context_manager import ContextManager, Message
from .sub_agent import SubAgentManager, SubAgentTask, SubAgentResult, SubAgentStatus
from .tools import ToolExecutor, TOOL_DEFINITIONS

__all__ = [
    "AgentCore",
    "AgentResponse",
    "AgentMode",
    "Config",
    "LLMConfig",
    "AgentConfig",
    "get_config",
    "ContextManager",
    "Message",
    "SubAgentManager",
    "SubAgentTask",
    "SubAgentResult",
    "SubAgentStatus",
    "ToolExecutor",
    "TOOL_DEFINITIONS",
]
