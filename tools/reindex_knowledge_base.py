import os
import sys
import hashlib
import uuid
import httpx
from typing import List, Optional
from pypdf import PdfReader
from docx import Document

SOURCE_DIR = "SMART_ENTERPRISE/ai/knowledge_base/source"
QDRANT_HOST = os.getenv("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
ACTIVE_COLLECTION = os.getenv("QDRANT_COLLECTION", "rag_real_estate_active")
STAGING_COLLECTION = "rag_real_estate_v2_staging"
OLLAMA_EMBED_URL = f"{os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')}/api/embeddings"
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "mxbai-embed-large:latest")

DOC_MAPPING = {
    "Керівництво_з_перевірки_документів.pdf": "doc-01-verification-guide",
    "Опис_послуг_агентства.docx": "doc-02-services-description",
    "Памятка_для_покупця.pdf": "doc-03-buyer-memo",
    "Порядок_підписання_договору.docx": "doc-04-contract-signing-order",
    "Правила_конфіденційності.pdf": "doc-05-privacy-policy",
    "Шаблон_договору_оренди.docx": "doc-06-lease-contract-template",
}


def extract_text_from_file(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    text = ""
    if ext == ".pdf":
        reader = PdfReader(file_path)
        pages = []
        for p in reader.pages:
            t = p.extract_text() or ""
            pages.append(t)
        text = "\n\n".join(pages)
    elif ext == ".docx":
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs)

    lines = [line.strip() for line in text.split("\n")]
    clean_lines = [l for l in lines if l]
    return "\n".join(clean_lines)


def split_text_into_chunks(text: str, chunk_size: int = 450, overlap: int = 100) -> List[str]:
    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size
        if end >= text_len:
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break

        split_pos = text.rfind("\n", start + chunk_size // 2, end)
        if split_pos == -1:
            split_pos = text.rfind(". ", start + chunk_size // 2, end)
        if split_pos != -1:
            end = split_pos + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = max(start + 1, end - overlap)

    return chunks


def get_embedding(text: str) -> List[float]:
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            OLLAMA_EMBED_URL,
            json={"model": EMBED_MODEL, "prompt": text}
        )
        if r.status_code != 200:
            raise RuntimeError(f"Ollama embedding failed ({r.status_code}): {r.text}")
        return r.json().get("embedding", [])


def _populate_collection(collection_name: str, points: List[dict]) -> int:
    with httpx.Client(timeout=30.0) as client:
        client.delete(f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{collection_name}")
        create_resp = client.put(
            f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{collection_name}",
            json={
                "vectors": {
                    "size": 1024,
                    "distance": "Cosine"
                }
            }
        )
        if create_resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create Qdrant collection '{collection_name}': {create_resp.text}")

        batch_size = 10
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            upload_resp = client.put(
                f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{collection_name}/points",
                json={"points": batch}
            )
            if upload_resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to upload points batch to '{collection_name}': {upload_resp.text}")

        info_resp = client.get(f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{collection_name}")
        info = info_resp.json().get("result", {})
        return int(info.get("points_count", 0))


def reindex_all(target_collections: Optional[List[str]] = None) -> int:
    collections = target_collections or [ACTIVE_COLLECTION, STAGING_COLLECTION]
    print(f"=== REINDEXING KNOWLEDGE BASE INTO {collections} ===")

    points = []
    files = sorted(os.listdir(SOURCE_DIR))
    print(f"Found {len(files)} files in source directory '{SOURCE_DIR}'.")

    total_chunks = 0
    for fname in files:
        fpath = os.path.join(SOURCE_DIR, fname)
        if not os.path.isfile(fpath) or fname not in DOC_MAPPING:
            continue

        doc_id = DOC_MAPPING[fname]
        source_format = os.path.splitext(fname)[1].lstrip(".").lower()
        print(f"\nProcessing [{doc_id}]: {fname}...")

        raw_text = extract_text_from_file(fpath)
        chunks = split_text_into_chunks(raw_text, chunk_size=450, overlap=100)
        print(f" -> Extracted {len(raw_text)} chars, split into {len(chunks)} chunks.")

        for idx, chunk_text in enumerate(chunks):
            content_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            pt_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"rentkom:{doc_id}:{idx}"))
            vector = get_embedding(chunk_text)

            point = {
                "id": pt_id,
                "vector": vector,
                "payload": {
                    "content": chunk_text,
                    "text": chunk_text,
                    "document_id": doc_id,
                    "document_name": fname,
                    "source": fname,
                    "chunk_index": idx,
                    "source_format": source_format,
                    "content_hash": content_hash,
                    "embedding_model": EMBED_MODEL,
                    "ingestion_version": "2.0.0"
                }
            }
            points.append(point)
            total_chunks += 1

    for col in collections:
        pts_count = _populate_collection(col, points)
        print(f"[OK] Collection '{col}' indexed with {pts_count} deterministic points.")

    return total_chunks


if __name__ == "__main__":
    reindex_all()
