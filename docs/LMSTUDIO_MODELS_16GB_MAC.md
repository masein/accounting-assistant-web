# LM Studio models for 16GB RAM Mac

Use these in LM Studio so the accounting assistant responds quickly and reliably. Set the model in **LM Studio** (load it and keep the server running), then in this app set `LM_STUDIO_MODEL` in `.env` to the **exact name** LM Studio shows (e.g. `lmstudio-community/granite-4-7b-instruct-q4_k_m`).

## Recommended (faster, good at JSON)

- **Granite 4.0 7B** (e.g. `granite-4-7b-instruct` or community GGUF)  
  - Good at JSON and instruction following. Fits 16GB in Q4/Q5. Prefer **instruct** variant.

- **Qwen3 4B (non‑thinking)**  
  - If you see `qwen3-4b` or `Qwen3-4B` **without** “thinking” in the name: faster, fewer tokens, often better for short JSON. Use this instead of the “thinking” variant on 16GB.

- **Ministral 3B or 8B**  
  - Small and fast. Search “Ministral” in LM Studio; 3B is lightest, 8B better quality.

- **Phi-4 3B** or **Phi-4 mini**  
  - Very light, good for simple tasks. Search “phi-4” in LM Studio.

- **Mistral 7B Instruct**  
  - Classic 7B; use Q4_K_M or Q5_K_M for 16GB. Search “Mistral 7B” in LM Studio.

- **Gemma 3 4B** (e.g. `gemma-3-4b`)  
  - Fits 16GB, decent instruction following.

## Avoid on 16GB for speed

- **“Thinking” / CoT models** (e.g. `qwen3-4b-**thinking**`, DeepSeek-R1, long-reasoning): they use many tokens and can be slow or time out. Prefer a normal instruct model.

## How to switch model in this app

1. In LM Studio: download and load the model, start the local server.
2. In this project `.env` (or environment):
   - `LM_STUDIO_BASE_URL` = your LM Studio server (e.g. `http://127.0.0.1:1234`).
   - `LM_STUDIO_MODEL` = the **exact** model name LM Studio shows (e.g. `lmstudio-community/granite-4-7b-instruct-q4_k_m` or `qwen/qwen3-4b`).
3. Restart the accounting-assistant app.

If you’re not sure of the exact name: in LM Studio, open the **Developer** tab or check the loaded model; the name shown there is what to put in `LM_STUDIO_MODEL`.
