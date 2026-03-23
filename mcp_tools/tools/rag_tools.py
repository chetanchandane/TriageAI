"""
MCP RAG tools: ChromaDB-backed policy search (local fallback).

DEPRECATED (Sprint 4): When the Chroma MCP Server is running, the triage agent
uses chroma_query_documents discovered via MultiServerMCPClient instead of this
module. This local wrapper is retained as a fallback for when the MCP server is
unavailable — the graph builder will include search_hospital_policy from
graph/nodes.py in TRIAGE_TOOLS automatically.

Wraps get_relevant_policy from agents/policy_agent for agent/MCP use.
ChromaDB initialization is lazy-loaded in agents/policy_agent.
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
