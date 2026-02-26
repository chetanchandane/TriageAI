"""
Verification test script for Sprint 1 MCP tools.
Tests that ChromaDB and Supabase queries work through the new tool functions.

Run from project root:
    python -m pytest tests/test_tools.py -v
    # or directly:
    python tests/test_tools.py
"""
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Tool A: get_patient_history (Supabase)
# ---------------------------------------------------------------------------

def test_get_patient_history_returns_string():
    """get_patient_history should always return a string (even if empty)."""
    from mcp.tools.database_tools import get_patient_history
    result = get_patient_history("PAT-NONEXISTENT")
    assert isinstance(result, str), f"Expected str, got {type(result)}"
    print(f"  [PASS] get_patient_history returns str: {repr(result)[:80]}")


def test_get_patient_history_with_valid_id():
    """If Supabase is configured, query a real patient_id (may still return empty)."""
    from mcp.tools.database_tools import get_patient_history
    result = get_patient_history("PAT-TEST0001")
    assert isinstance(result, str)
    print(f"  [PASS] get_patient_history (real query): {repr(result)[:80]}")


# ---------------------------------------------------------------------------
# Tool B: search_hospital_policy (ChromaDB RAG)
# ---------------------------------------------------------------------------

def test_search_hospital_policy_returns_list():
    """search_hospital_policy should return a list of strings."""
    from mcp.tools.rag_tools import search_hospital_policy
    result = search_hospital_policy("prescription refill")
    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert len(result) > 0, "Expected at least 1 policy chunk"
    for chunk in result:
        assert isinstance(chunk, str), f"Each chunk should be str, got {type(chunk)}"
    print(f"  [PASS] search_hospital_policy returned {len(result)} chunks")


def test_search_hospital_policy_top_k():
    """search_hospital_policy should respect the top_k parameter."""
    from mcp.tools.rag_tools import search_hospital_policy
    result_1 = search_hospital_policy("appointment", top_k=1)
    result_3 = search_hospital_policy("appointment", top_k=3)
    assert len(result_1) == 1, f"Expected 1 chunk with top_k=1, got {len(result_1)}"
    assert len(result_3) == 3, f"Expected 3 chunks with top_k=3, got {len(result_3)}"
    print(f"  [PASS] top_k=1 -> {len(result_1)} chunk, top_k=3 -> {len(result_3)} chunks")


def test_search_hospital_policy_relevance():
    """Policy search for 'emergency' should return the emergency policy snippet."""
    from mcp.tools.rag_tools import search_hospital_policy
    result = search_hospital_policy("emergency life threatening")
    texts = " ".join(result).lower()
    assert "emergency" in texts or "911" in texts or "er" in texts, \
        f"Expected emergency-related content, got: {texts[:200]}"
    print(f"  [PASS] 'emergency' query returns relevant policy")


# ---------------------------------------------------------------------------
# Tool C: get_available_slots (Scheduling)
# ---------------------------------------------------------------------------

def test_get_available_slots_returns_list():
    """get_available_slots should return the hardcoded slot list."""
    from mcp.tools.database_tools import get_available_slots
    result = get_available_slots()
    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert result == ["Mon 10am", "Wed 2pm", "Fri 9am"], f"Unexpected slots: {result}"
    print(f"  [PASS] get_available_slots: {result}")


# ---------------------------------------------------------------------------
# MCP Server: verify all tools are exposed
# ---------------------------------------------------------------------------

def test_mcp_server_exports():
    """mcp.server should export all three core tools."""
    from mcp.server import get_patient_history, search_hospital_policy, get_available_slots
    assert callable(get_patient_history)
    assert callable(search_hospital_policy)
    assert callable(get_available_slots)
    print(f"  [PASS] mcp.server exports all 3 tools")


# ---------------------------------------------------------------------------
# Schema verification
# ---------------------------------------------------------------------------

def test_schemas_importable():
    """SafetyResult and TriageResult should be importable from schemas package."""
    from schemas import SafetyResult, TriageResult
    sr = SafetyResult(is_potential_emergency=False, reason="test", triggered_by="none")
    tr = TriageResult(
        intent="Test",
        confidence=0.9,
        urgency="LOW",
        summary="Test summary",
        checklist=["item1"],
        recommended_queue="Front Desk",
    )
    assert sr.is_potential_emergency is False
    assert tr.urgency == "LOW"
    print(f"  [PASS] Schemas importable and constructable")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    test_get_patient_history_returns_string,
    test_get_patient_history_with_valid_id,
    test_search_hospital_policy_returns_list,
    test_search_hospital_policy_top_k,
    test_search_hospital_policy_relevance,
    test_get_available_slots_returns_list,
    test_mcp_server_exports,
    test_schemas_importable,
]


def main():
    print("=" * 60)
    print("TriageAI Sprint 1 — MCP Tool Verification")
    print("=" * 60)

    passed = 0
    failed = 0
    for test_fn in ALL_TESTS:
        name = test_fn.__name__
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1

    print()
    print("-" * 60)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed == 0:
        print("All tests passed!")
    else:
        print(f"{failed} test(s) FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
