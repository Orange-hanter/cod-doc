"""LLM adapters package (PCA-300..302, proposal 10).

Public API:
    from cod_doc.agent.adapters import get_adapter, get_adapter_from_config
    from cod_doc.agent.adapters.base import LLMAdapter, ChatResponse, MockAdapter
    from cod_doc.agent.adapters.mock import MockAdapter
"""

from cod_doc.agent.adapters.base import (
    AdapterCapabilities,
    ChatChoice,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    FunctionCall,
    LLMAdapter,
    ToolCall,
)
from cod_doc.agent.adapters.registry import (
    get_adapter,
    get_adapter_from_config,
    list_adapters,
    register_adapter,
)

__all__ = [
    "AdapterCapabilities",
    "ChatChoice",
    "ChatMessage",
    "ChatResponse",
    "ChatUsage",
    "FunctionCall",
    "LLMAdapter",
    "ToolCall",
    "get_adapter",
    "get_adapter_from_config",
    "list_adapters",
    "register_adapter",
]
