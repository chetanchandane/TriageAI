"""
Seed the persistent ChromaDB vector store from hospital policy documents.

Supports Markdown (.md/.markdown), plain text (.txt), and PDF (.pdf) files.
Markdown is preferred — it extracts cleanly, unlike PDFs whose text layer often
injects per-word whitespace noise.

Usage (run from project root):
    python scripts/seed_policy.py                # skip if collection already populated
    python scripts/seed_policy.py --force        # wipe collection and reseed

Place policy files in data/policies/ before running.
Output directory: ./data/vector_store/
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb

VECTOR_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "vector_store",
)
POLICIES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "policies",
)
COLLECTION_NAME = "hospital_policies"

CHUNK_SIZE = 700      # characters per chunk
CHUNK_OVERLAP = 100   # overlap between consecutive chunks


# File extensions we know how to read.
SUPPORTED_EXTS = (".md", ".markdown", ".txt", ".pdf")


def _extract_text_from_pdf(path: str) -> str:
    """Extract plain text from a PDF file using pypdf.

    Collapses pypdf's per-word whitespace noise so chunks are clean prose.
    """
    import re

    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except Exception as e:
        print(f"  [warn] Could not parse {path}: {e}")
        return ""


def _extract_text_from_textfile(path: str) -> str:
    """Read a Markdown or plain-text file as UTF-8 (lenient on bad bytes).

    Markdown is kept as-is — headings and lists are meaningful retrieval signal,
    and the text extracts cleanly without the whitespace noise PDFs suffer from.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception as e:
        print(f"  [warn] Could not read {path}: {e}")
        return ""


def _extract_text(path: str) -> str:
    """Dispatch to the right extractor based on file extension."""
    if path.lower().endswith(".pdf"):
        return _extract_text_from_pdf(path)
    return _extract_text_from_textfile(path)


def _chunk_text(text: str, source: str) -> list[dict]:
    """Split text into overlapping chunks, tagging each with its source filename."""
    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk = text[start:end].strip()
        if chunk:
            chunks.append({"text": chunk, "source": source, "chunk_idx": idx})
            idx += 1
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def load_documents() -> list[dict]:
    """Walk POLICIES_DIR, parse every supported file, return list of chunk dicts."""
    if not os.path.isdir(POLICIES_DIR):
        print(f"[error] Policies directory not found: {POLICIES_DIR}")
        print("  Create data/policies/ and place your policy files there.")
        sys.exit(1)

    files = [
        f for f in os.listdir(POLICIES_DIR)
        if f.lower().endswith(SUPPORTED_EXTS)
    ]
    if not files:
        print(f"[error] No supported files ({', '.join(SUPPORTED_EXTS)}) in {POLICIES_DIR}")
        sys.exit(1)

    all_chunks = []
    for fname in sorted(files):
        path = os.path.join(POLICIES_DIR, fname)
        print(f"  Parsing: {fname}")
        text = _extract_text(path)
        if not text:
            print(f"  [skip] No text extracted from {fname}")
            continue
        chunks = _chunk_text(text, source=fname)
        print(f"    → {len(chunks)} chunks")
        all_chunks.extend(chunks)

    return all_chunks


def seed(force: bool = False):
    os.makedirs(VECTOR_STORE_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=VECTOR_STORE_PATH)

    if force:
        print(f"--force: deleting existing '{COLLECTION_NAME}' collection.")
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    coll = client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"description": "Hospital policy documents"},
    )

    if not force and coll.count() > 0:
        print(f"Collection '{COLLECTION_NAME}' already has {coll.count()} chunks — skipping.")
        print("  Run with --force to wipe and reseed.")
        return

    print(f"Loading policy documents from: {POLICIES_DIR}")
    chunks = load_documents()

    if not chunks:
        print("[error] No chunks produced — nothing seeded.")
        sys.exit(1)

    ids = [f"{c['source']}__chunk_{c['chunk_idx']}" for c in chunks]
    documents = [c["text"] for c in chunks]
    metadatas = [{"source": c["source"], "chunk_idx": c["chunk_idx"]} for c in chunks]

    coll.upsert(documents=documents, ids=ids, metadatas=metadatas)
    print(f"\nSeeded {len(chunks)} chunks from {len(set(c['source'] for c in chunks))} file(s) into '{COLLECTION_NAME}'.")
    print(f"Store location: {VECTOR_STORE_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed ChromaDB with hospital policy PDFs.")
    parser.add_argument("--force", action="store_true", help="Wipe existing collection and reseed.")
    args = parser.parse_args()
    seed(force=args.force)
