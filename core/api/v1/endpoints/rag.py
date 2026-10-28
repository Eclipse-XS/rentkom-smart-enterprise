from fastapi import APIRouter
from core.schemas import RAGQueryRequest, RAGQueryResponse
from core.services.rag_service import query_rag

router = APIRouter()

@router.post("/ask", response_model=RAGQueryResponse)
async def ask_rag(query: RAGQueryRequest):
    """Executes grounded RAG retrieval and inference, applying guardrails and resilient fallback."""
    return await query_rag(query.question, query.conversation_history)
