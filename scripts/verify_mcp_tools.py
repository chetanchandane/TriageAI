#!/usr/bin/env python3
"""
Verification script for Sprint 1 MCP tools.
Run from project root: python scripts/verify_mcp_tools.py
Tests get_patient_history (Supabase), search_hospital_policy (ChromaDB), get_available_slots.
"""
import os
import sys

# Run from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    print("=== MCP Tools Verification ===\n")

    # 1. get_available_slots (no external deps)
    print("1. get_available_slots (database_tools)")
    try:
        from mcp.tools.database_tools import get_available_slots
        slots = get_available_slots()
        print(f"   Result: {slots}")
        assert isinstance(slots, list) and len(slots) == 3
        print("   OK\n")
    except Exception as e:
        print(f"   FAIL: {e}\n")
        return 1

    # 2. get_patient_history (Supabase)
    print("2. get_patient_history (database_tools -> Supabase profiles)")
    try:
        from mcp.tools.database_tools import get_patient_history
        # Use a sample patient_id; may return "" if no row or no medical_history column
        out = get_patient_history("PAT-EXAMPLE")
        print(f"   Result (patient_id=PAT-EXAMPLE): {repr(out)}")
        assert isinstance(out, str)
        print("   OK\n")
    except Exception as e:
        print(f"   FAIL: {e}\n")
        return 1

    # 3. search_hospital_policy (ChromaDB / policy_agent)
    print("3. search_hospital_policy (rag_tools -> ChromaDB)")
    try:
        from mcp.tools.rag_tools import search_hospital_policy
        chunks = search_hospital_policy("prescription refill", top_k=3)
        print(f"   Result (query='prescription refill', top_k=3): {len(chunks)} chunks")
        for i, c in enumerate(chunks):
            print(f"     [{i+1}] {c[:80]}...")
        assert isinstance(chunks, list)
        print("   OK\n")
    except Exception as e:
        print(f"   FAIL: {e}\n")
        return 1

    print("=== All MCP tool checks passed. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
