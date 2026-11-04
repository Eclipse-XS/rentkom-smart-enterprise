from fastapi import APIRouter, Depends
from core.schemas import RAGQueryRequest, RAGQueryResponse
from core.services.rag_service import query_rag
from core.security import check_rag_rate_limit

router = APIRouter()

@router.post("/ask", response_model=RAGQueryResponse, dependencies=[Depends(check_rag_rate_limit)])
async def ask_rag(query: RAGQueryRequest):
    """Executes grounded RAG retrieval and inference, applying guardrails and resilient fallback."""
    return await query_rag(query.question, query.conversation_history)
