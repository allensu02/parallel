# GPT-OSS 120B on Modal (OpenAI-compatible)

Deploys [OpenAI GPT-OSS 120B](https://huggingface.co/openai/gpt-oss-120b) on [Modal](https://modal.com) using vLLM, exposing an **OpenAI-compatible** HTTP API.

Based on [Modal's GPT-OSS inference example](https://modal.com/docs/examples/gpt_oss_inference) and the [vLLM GPT-OSS recipe](https://docs.vllm.ai/projects/recipes/en/latest/OpenAI/GPT-OSS.html).

## Prerequisites

- [Modal account](https://modal.com) and CLI: `pip install modal` then `modal setup`
- Hugging Face access to `openai/gpt-oss-120b` (and `nvidia/gpt-oss-120b-Eagle3-v2` if using speculative decoding). Set `HF_TOKEN` if required.

## Deploy (production endpoint)

```bash
cd modal-gpt-oss-120b
modal deploy gpt_oss_inference.py
```

This builds the image (first time may take a while), deploys the app, and gives you a **web URL** for the server. That URL is your OpenAI-compatible base (e.g. `https://<your-app>--serve.modal.run`).

- **Chat completions:** `POST {BASE_URL}/v1/chat/completions`
- **Health:** `GET {BASE_URL}/health`

Use it with any OpenAI-compatible client by setting `base_url` to that URL.

### Quick test with curl

```bash
curl -X POST "https://joe5050-li--gpt-oss-120b-inference-serve.modal.run/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": "What is the capital of France?"}], "stream": false}'
```

## Run locally (test)

Spins up a one-off replica and runs the test entrypoint (healthcheck + streaming chat):

```bash
modal run gpt_oss_inference.py
```

With a custom prompt:

```bash
modal run gpt_oss_inference.py --user-content "What is the capital of France?"
```

## Config

- **Model:** `openai/gpt-oss-120b` (revision in script, default `main`)
- **GPU:** 1x B200 (Blackwell)
- **Speculative decoding:** `nvidia/gpt-oss-120b-Eagle3-v2` (Eagle3)
- **FAST_BOOT:** Set to `True` in the script for quicker startup during development (disables compilation/CUDA graphs).

## Use as browser-use LLM

The treehacks backend can use this deployment as the browser-use agent LLM. In `backend/.env` set:

- `BROWSER_USE_LLM_PROVIDER=openai_compat`
- `BROWSER_USE_LLM_BASE_URL=https://joe5050-li--gpt-oss-120b-inference-serve.modal.run/v1`
- `BROWSER_USE_LLM_MODEL=openai/gpt-oss-120b`
- `BROWSER_USE_LLM_API_KEY=dummy` (or leave unset)

Use your actual Modal web URL if different. With `AGENT_BACKEND=browser-use`, the swarm will use this endpoint for the agent.

## Optional: no speculative decoding

To disable speculative decoding (e.g. if the draft model is unavailable), remove or comment out the `--speculative-config` line in the `serve()` function in `gpt_oss_inference.py`.
