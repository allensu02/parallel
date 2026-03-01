"""
Deploy OpenAI GPT-OSS 120B on Modal with an OpenAI-compatible endpoint.

Based on: https://modal.com/docs/examples/gpt_oss_inference
vLLM recipe: https://docs.vllm.ai/projects/recipes/en/latest/OpenAI/GPT-OSS.html

Deploy:
  modal deploy gpt_oss_inference.py

Run locally (spins up a replica and runs the test entrypoint):
  modal run gpt_oss_inference.py

Optional: test with custom prompt
  modal run gpt_oss_inference.py --user-content "What is the capital of France?"
"""

import json
import time
from datetime import datetime, timezone
from typing import Any

import modal

# ---------------------------------------------------------------------------
# Container image
# ---------------------------------------------------------------------------

vllm_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.8.1-devel-ubuntu22.04",
        add_python="3.12",
    )
    .entrypoint([])
    .uv_pip_install(
        "vllm==0.13.0",
        "huggingface_hub[hf_transfer]==0.36.0",
    )
    .env({"VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8": "1"})  # Blackwell MoE kernels
)

# ---------------------------------------------------------------------------
# Model: GPT-OSS 120B (OpenAI open-weight reasoning model)
# ---------------------------------------------------------------------------

MODEL_NAME = "openai/gpt-oss-120b"
# Pin revision for reproducibility; use "main" or a specific commit from Hugging Face.
MODEL_REVISION = "main"

# ---------------------------------------------------------------------------
# Volumes for caching weights and vLLM artifacts
# ---------------------------------------------------------------------------

hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("vllm-cache", create_if_missing=True)
flashinfer_cache_vol = modal.Volume.from_name("flashinfer-cache", create_if_missing=True)

# ---------------------------------------------------------------------------
# vLLM config (from Modal example + vLLM GPT-OSS 120B recipe)
# ---------------------------------------------------------------------------

VLLM_CONFIG = {
    "stream-interval": 20,
    "kv-cache-dtype": "fp8",
    "max-num-batched-tokens": 8192,
    "max-model-len": 32768,
}

# Speculative decoding for 120B (from vLLM recipe: nvidia/gpt-oss-120b-Eagle3-v2)
SPECULATIVE_CONFIG = {
    "model": "nvidia/gpt-oss-120b-Eagle3-v2",
    "num_speculative_tokens": 7,
    "method": "eagle3",
    "draft_tensor_parallel_size": 1,
}

COMPILATION_CONFIG = {
    "pass_config": {"fuse_allreduce_rms": True, "eliminate_noops": True},
}

# Set True for faster iteration (disables compilation/CUDA graphs)
FAST_BOOT = False

MAX_INPUTS = 32
VLLM_CONFIG["max-cudagraph-capture-size"] = MAX_INPUTS

# ---------------------------------------------------------------------------
# App and serve
# ---------------------------------------------------------------------------

app = modal.App("gpt-oss-120b-inference")

N_GPU = 1
MINUTES = 60
VLLM_PORT = 8000


@app.function(
    image=vllm_image,
    gpu=f"B200:{N_GPU}",
    scaledown_window=10 * MINUTES,
    timeout=30 * MINUTES,
    volumes={
        "/root/.cache/huggingface": hf_cache_vol,
        "/root/.cache/vllm": vllm_cache_vol,
        "/root/.cache/flashinfer": flashinfer_cache_vol,
    },
)
@modal.concurrent(max_inputs=MAX_INPUTS)
@modal.web_server(port=VLLM_PORT, startup_timeout=30 * MINUTES)
def serve():
    import subprocess

    cmd = [
        "vllm",
        "serve",
        MODEL_NAME,
        "--revision",
        MODEL_REVISION,
        "--served-model-name",
        MODEL_NAME,
        "llm",
        "--host",
        "0.0.0.0",
        "--port",
        str(VLLM_PORT),
        # Omit --async-scheduling: with speculative decoding, vLLM then rejects
        # requests that use frequency_penalty, presence_penalty, stop, or structured
        # outputs (e.g. from browser-use / OpenAI client).
    ]

    cmd += ["--enforce-eager" if FAST_BOOT else "--no-enforce-eager"]
    cmd += ["--tensor-parallel-size", str(N_GPU)]
    cmd += ["--compilation-config", json.dumps(COMPILATION_CONFIG)]
    cmd += ["--speculative-config", json.dumps(SPECULATIVE_CONFIG)]
    cmd += [item for k, v in VLLM_CONFIG.items() for item in (f"--{k}", str(v))]

    print(*cmd)
    subprocess.Popen(cmd)


# ---------------------------------------------------------------------------
# Local test entrypoint (healthcheck + chat completions)
# ---------------------------------------------------------------------------

@app.local_entrypoint()
async def test(test_timeout: int = 30 * MINUTES, user_content: str | None = None, twice: bool = True):
    import aiohttp

    url = await serve.get_web_url.aio()

    system_prompt = {
        "role": "system",
        "content": f"""You are ChatModal, a large language model trained by Modal.
Knowledge cutoff: 2024-06
Current date: {datetime.now(timezone.utc).date()}
Reasoning: low
# Valid channels: analysis, commentary, final. Channel must be included for every message.
Calls to these tools must go to the commentary channel: 'functions'.""",
    }

    if user_content is None:
        user_content = "Explain what the Singular Value Decomposition is."

    messages = [
        system_prompt,
        {"role": "user", "content": user_content},
    ]

    async with aiohttp.ClientSession(base_url=url) as session:
        print(f"Health check: {url}")
        async with session.get("/health", timeout=test_timeout - 1 * MINUTES) as resp:
            assert resp.status == 200, f"Health check failed: {resp.status}"
        print("Health check OK")

        print("Sending chat request...")
        await _send_request(session, MODEL_NAME, messages)

        if twice:
            messages[0]["content"] += "\nTalk like a pirate, matey."
            print("Sending second request...")
            await _send_request(session, MODEL_NAME, messages)


async def _send_request(
    session: "aiohttp.ClientSession", model: str, messages: list
) -> None:
    import aiohttp

    payload: dict[str, Any] = {"messages": messages, "model": model, "stream": True}
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}

    t = time.perf_counter()
    async with session.post(
        "/v1/chat/completions", json=payload, headers=headers, timeout=10 * MINUTES
    ) as resp:
        async for raw in resp.content:
            resp.raise_for_status()
            line = raw.decode().strip()
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data: "):
                line = line[len("data: ") :]
            chunk = json.loads(line)
            assert chunk.get("object") == "chat.completion.chunk"
            delta = chunk["choices"][0]["delta"]
            if "content" in delta:
                print(delta["content"], end="")
            elif "reasoning_content" in delta:
                print(delta["reasoning_content"], end="")
            elif not delta:
                print()
            else:
                raise ValueError(f"Unsupported delta: {delta}")
    print()
    print(f"Time to last token: {time.perf_counter() - t:.2f}s")
