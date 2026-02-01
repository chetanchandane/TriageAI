# Development Journal & Decision Log
It tracks *why* you made certain decisions—this is what professors love to see in the final report.
## Project Setup (Feb 1, 2026)
- **Tool Stack:** Finalised the tools that I will be using for this project.
- **Repository Initialized:** Set up core folder structure.
- **Orchestration Choice:** Decided on **LangGraph** over CrewAI/LangChain to allow for cyclic error handling and better state management during the "Staff Review" phase.
<!-- - **Observability:** Integrated **LangSmith** to ensure every LLM call is traceable for the "Audit & Metrics" layer. -->
- **Environment:** Configured `.env` for Gemeni(Temporary).

## Phase 1 Goals: The Triage Node
- [X] Define Pydantic schema for structured output.
- [X] Craft the "Triage System Prompt" with clinical urgency rules.
- [X] Build a local test script to verify categorization accuracy.
- [ ] Get started with setting up LangGraph(Basic workflow)
- [ ] Integrate LangSmith, to improve observability which will help me with debugging.


## Current Roadblocks / Notes
- Need to ensure the "Safety Screening" node has 0% False Negatives for emergencies.
- Need to read more research on this topic. (I have compiled some information in a word doc, i will be pasting a link to that doc here(I will make it public))
: [Research Notes](https://docs.google.com/document/d/1Gr3C9JskVDrQheYMiz4jvEEy_VRp9OTWS1hD-DlHIaM/edit?usp=sharing)