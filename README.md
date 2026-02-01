# TriageAI - Agentic Patient Portal Triage System 

An autonomous, multi-agent AI system designed to transform free-text patient messages into structured clinical actions. This project serves as a Master’s Capstone at the Rochester Institute of Technology (RIT).

## 🚀 Overview
Traditional patient portals often lead to administrative bottlenecks. This system utilizes **Agentic AI** to:
- **Triage:** Categorize intent and urgency (Normal to Emergency).
- **Route:** Assign messages to specific clinical queues.
- **Support:** Generate policy-grounded draft replies and missing-information checklists.

## 🛠️ Technical Stack
- **Orchestration:** LangGraph (Multi-agent state machine)
- **Reasoning:** Llama 3.1 via Groq API
- **Observability:** LangSmith (Tracing and Evaluation)
- **Database:** Supabase (PostgreSQL for Audit & Metrics)
- **Interface:** Streamlit (Patient and Staff Portals)

## 🏗️ Architecture


1. **Message Intake:** Patient submits free-text.
2. **Safety Agent:** Immediate screening for life-threatening emergencies.
3. **Triage Agent:** Intent classification and urgency scoring.
4. **Policy Agent (RAG):** Contextual retrieval of clinic rules via ChromaDB.
5. **Human-in-the-loop:** Staff review/edit before final action.
