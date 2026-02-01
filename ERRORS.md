# Errors Faced vs Their Resolution

## Triage Logic Implementation (Feb 1, 2026)

- **Challenge**: Pydantic ValidationError due to Markdown backticks in JSON response. 
- **Fix**: Implemented response_schema in Gemini generate_content config to utilize native structured output, removing the need for manual regex cleaning or string parsing.

- **Model:** Upgraded to **Gemini 3 Flash** for better agentic reasoning.
- **Structured Output:** Implemented native `response_schema` via the Google GenAI SDK.
- **Optimization:** Moved triage rules from the user prompt to `system_instruction` to improve category adherence and reduce token usage.
- **Bug Fix:** Resolved Pydantic validation errors by utilizing `response.parsed`, which eliminates the need for manual JSON regex cleaning.