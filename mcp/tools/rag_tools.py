"""
MCP RAG tools: ChromaDB-backed policy search.
Wraps get_relevant_policy from agents/policy_agent for agent/MCP use.
ChromaDB initialization is lazy-loaded in agents/policy_agent; this module exposes search_hospital_policy.
"""
from typing import List


def search_hospital_policy(query: str, top_k: int = 3) -> List[str]:
    """
    RAG search: wrap get_relevant_policy from policy_agent.
    Input: query string. Returns top_k (default 3) chunks from ChromaDB.
    """
    try:
        from agents.policy_agent import get_relevant_policy
        return get_relevant_policy(query, "", top_k=top_k)
    except Exception:
        return []
