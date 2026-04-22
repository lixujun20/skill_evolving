# Experiment Settings (Exp 7+)

## ⚠️ IMPORTANT: All experiments MUST use these settings

### LLM Configuration
- **Model**: `glm-4.7` via BigModel API
- **max_tokens**: `8000` (reduced from 16000 to avoid per-call timeouts)
- **temperature**: `0.0`
- **Config file**: `config/config.toml` → `[llm.bigmodel]`

### Executor Settings
- **MAX_AGENT_STEPS**: `15` (increased from 8 to compensate for lower max_tokens)
- **CODE_EXEC_TIMEOUT**: `30s` (per code block execution)
- **LLM_CALL_TIMEOUT**: `300s` (per API call; with max_tokens=8000 at ~55 tok/s → ~145s typical, well within limit)
- **LLM_TIMEOUT_RETRIES**: `3` (retry each timed-out API call up to 3 times)
- **Config file**: `academic/config.py`

### Timeout Handling
- Each LLM API call retries up to 3 times on timeout
- If 3 **consecutive** problems timeout (all 3 retries exhausted), the experiment HALTS
- This prevents wasting API credits on systematic failures

### Rationale
Previous experiments (Exp 4-6) showed:
- `max_tokens=16000` caused the LLM to generate monolithic 16K-token responses
- At BigModel's ~55-77 tok/s generation speed, 16K tokens = 206-291s
- Some calls exceeded the 300s per-call timeout, producing 0-token results
- Reducing to 8000 forces shorter per-step responses (~100-145s per call)
- Increasing MAX_AGENT_STEPS to 15 allows more interaction rounds to compensate
