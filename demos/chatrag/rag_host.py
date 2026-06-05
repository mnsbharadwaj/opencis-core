"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

#!/usr/bin/env python

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

import click
import uvicorn
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from langchain.chains.retrieval_qa.base import RetrievalQA
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.document_loaders import PyPDFLoader, TextLoader

from opencis.cpu import CPU
from opencis.cxl.component.cxl_host import CxlHost, CxlHostConfig
from opencis.cxl.component.cxl_memory_hub import CxlMemoryHub, MEM_ADDR_TYPE
from opencis.drivers.cxl_bus_driver import CxlBusDriver
from opencis.drivers.cxl_mem_driver import CxlMemDriver
from opencis.drivers.pci_bus_driver import PciBusDriver
from opencis.util.logger import logger
from opencis.util.number_const import MB
from opencis.apps.backend.memory_backend import (
    AlignedMemoryBackend,
    StructuredMemoryAdapter,
)
from demos.chatrag.memory_vector_search import MemoryVectorSearch


@dataclass
class AppConfig:
    pci_cfg_base_addr: int = 0x10000000
    pci_cfg_size: int = 0x10000000
    pci_mmio_base_addr: int = 0xFE000000
    cxl_hpa_base_addr: int = 0x100000000000
    sys_mem_base_addr: int = 0xFFFF888000000000
    sys_mem_size: int = 2 * MB
    cxl_port_index: int = 0
    switch_port: int = 8000
    fastapi_port: int = 9000
    # ── LLM config ────────────────────────────────────────────────────────────
    # llm_source: one of  ollama | openai_compat | openai | gemini | anthropic |
    #                     huggingface
    #
    # "ollama"        – local Ollama server (Qwen, Gemma, Llama, …)
    # "openai_compat" – any OpenAI-compatible server (LM Studio, vLLM, etc.)
    #                   set base_url to your server, e.g. http://localhost:1234/v1
    # "openai"        – OpenAI cloud (GPT-4o, etc.)  needs api_token
    # "gemini"        – Google Gemini cloud            needs api_token
    # "anthropic"     – Anthropic Claude cloud         needs api_token
    # "huggingface"   – HuggingFace Hub inference      needs api_token
    llm_source: str = "ollama"
    model_name: str = "qwen2.5:7b"   # default: local Qwen via Ollama
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    api_token: str = ""              # required for cloud providers
    base_url: str = ""               # override for openai_compat / ollama endpoint


app_config = AppConfig()


def get_llm():
    """Return a LangChain LLM/ChatModel based on app_config.

    Supported sources
    -----------------
    ollama        – local Ollama server.  Works for Qwen, Gemma, Llama, Phi, …
                   Change model_name to any model you have pulled in Ollama.
    openai_compat – any server with an OpenAI-compatible /v1/chat/completions API
                   (LM Studio, vLLM, llama.cpp server, Qwen via vLLM …).
                   Set base_url to your server URL.
    openai        – OpenAI cloud.  Needs api_token.
    gemini        – Google Gemini cloud.  Needs api_token.
    anthropic     – Anthropic Claude cloud.  Needs api_token.
    huggingface   – HuggingFace Hub inference endpoint.  Needs api_token.
    """
    # pylint: disable=import-outside-toplevel
    source = app_config.llm_source.lower()
    model = app_config.model_name

    # ── Local: Ollama (Qwen, Gemma, Llama …) ─────────────────────────────────
    if source == "ollama":
        from langchain_ollama import ChatOllama
        kwargs = {"model": model}
        if app_config.base_url:
            kwargs["base_url"] = app_config.base_url  # default: http://localhost:11434
        return ChatOllama(**kwargs)

    # ── Local/Remote: any OpenAI-compatible API ───────────────────────────────
    # Works for: LM Studio, vLLM, llama.cpp --server, Qwen-via-vLLM, etc.
    if source == "openai_compat":
        from langchain_openai import ChatOpenAI
        base_url = app_config.base_url or "http://localhost:1234/v1"
        return ChatOpenAI(
            model=model,
            openai_api_key=app_config.api_token or "local",  # dummy key for local servers
            openai_api_base=base_url,
        )

    # ── Cloud: OpenAI ─────────────────────────────────────────────────────────
    if source == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            openai_api_key=app_config.api_token,
        )

    # ── Cloud: Google Gemini ──────────────────────────────────────────────────
    if source == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=app_config.api_token,
        )

    # ── Cloud: Anthropic Claude ───────────────────────────────────────────────
    if source == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            anthropic_api_key=app_config.api_token,
        )

    # ── Cloud: HuggingFace Hub ────────────────────────────────────────────────
    if source == "huggingface":
        from langchain_community.llms import HuggingFaceHub
        return HuggingFaceHub(
            repo_id=model,
            huggingfacehub_api_token=app_config.api_token,
        )

    raise ValueError(
        f"Unsupported llm_source='{source}'. "
        "Choose: ollama | openai_compat | openai | gemini | anthropic | huggingface"
    )


async def my_sys_sw_app(**kwargs):
    cxl_memory_hub: CxlMemoryHub = kwargs["cxl_memory_hub"]

    pci_bus_driver = PciBusDriver(cxl_memory_hub.get_root_complex())
    await pci_bus_driver.init(app_config.pci_mmio_base_addr)

    for i, device in enumerate(pci_bus_driver.get_devices()):
        cxl_memory_hub.add_mem_range(
            app_config.pci_cfg_base_addr + (i * app_config.pci_cfg_size),
            app_config.pci_cfg_size,
            MEM_ADDR_TYPE.CFG,
        )
        for bar in device.bars:
            if bar.base_address:
                cxl_memory_hub.add_mem_range(bar.base_address, bar.size, MEM_ADDR_TYPE.MMIO)

    cxl_bus_driver = CxlBusDriver(pci_bus_driver, cxl_memory_hub.get_root_complex())
    cxl_mem_driver = CxlMemDriver(cxl_bus_driver, cxl_memory_hub.get_root_complex())
    await cxl_bus_driver.init()
    await cxl_mem_driver.init()

    hpa_base = app_config.cxl_hpa_base_addr
    for device in cxl_mem_driver.get_devices():
        size = device.get_memory_size()
        success = await cxl_mem_driver.attach_single_mem_device(device, hpa_base, size)
        if success:
            cxl_memory_hub.add_mem_range(hpa_base, size, MEM_ADDR_TYPE.CXL_UNCACHED)
            hpa_base += size

    sys_mem_size = cxl_memory_hub.get_root_complex().get_sys_mem_size()
    cxl_memory_hub.add_mem_range(app_config.sys_mem_base_addr, sys_mem_size, MEM_ADDR_TYPE.DRAM)

    for r in cxl_memory_hub.get_memory_ranges():
        logger.info(
            f"[SYS-SW] MemoryRange: base: 0x{r.base_addr:X}, "
            f"size: 0x{r.size:X}, type: {r.addr_type}"
        )


def create_langchain_app(cpu: CPU) -> FastAPI:
    aligned = AlignedMemoryBackend(cpu.load, cpu.store, app_config.cxl_hpa_base_addr)
    store = StructuredMemoryAdapter(aligned)

    llm = get_llm()
    embedding_model = HuggingFaceEmbeddings(model_name=app_config.embedding_model)
    retriever = MemoryVectorSearch(store, embedding_model)

    retriever_chain = RetrievalQA.from_chain_type(llm=llm, retriever=retriever)

    app = FastAPI()
    app.state.retriever_chain = retriever_chain
    templates = Jinja2Templates(directory="templates")
    UPLOAD_DIR = Path("uploads")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        return templates.TemplateResponse("chatui/index.html", {"request": request})

    @app.post("/upload/")
    async def upload_file(file: UploadFile = File(...)):
        UPLOAD_DIR.mkdir(exist_ok=True)
        file_path = UPLOAD_DIR / file.filename

        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        ext = file_path.suffix.lower()
        loader = (
            PyPDFLoader(str(file_path))
            if ext == ".pdf"
            else TextLoader(str(file_path), encoding="utf-8")
        )
        documents = loader.load()
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = splitter.split_documents(documents)

        await retriever.add_documents(chunks)
        return JSONResponse({"message": "File uploaded and processed."})

    @app.delete("/upload/")
    async def delete_uploaded_files():
        if UPLOAD_DIR.exists():
            for file in UPLOAD_DIR.iterdir():
                if file.is_file():
                    file.unlink()

        retriever = app.state.retriever_chain.retriever
        retriever.clear()  # pylint: disable=no-member

        return JSONResponse({"message": "All uploaded files and vector DB entries deleted."})

    @app.post("/query/")
    async def query_route(request: Request):
        data = await request.json()
        question = data.get("question")
        if not question:
            return JSONResponse({"error": "No question provided"}, status_code=400)
        result = await retriever_chain.ainvoke(question)
        return JSONResponse({"answer": result["result"]})

    return app


async def my_user_app(**kwargs):
    cpu: CPU = kwargs["cpu"]
    app = create_langchain_app(cpu)
    config = uvicorn.Config(app, host="0.0.0.0", port=app_config.fastapi_port, loop="asyncio")
    server = uvicorn.Server(config)

    t = asyncio.create_task(server.serve())
    await asyncio.gather(t)


async def main():
    cxl_host_config = CxlHostConfig(
        port_index=app_config.cxl_port_index,
        sys_mem_size=app_config.sys_mem_size,
        user_app=my_user_app,
        sys_sw_app=my_sys_sw_app,
        host_name="LangchainHost",
        switch_port=app_config.switch_port,
        enable_hm=False,
    )
    host = CxlHost(cxl_host_config)
    host_task = asyncio.create_task(host.run())
    await host_task


@click.command()
@click.option("--switch_port", default=8000, type=int, help="CXL Switch port")
@click.option("--server-port", default=9000, type=int, help="Port to run the FastAPI server on.")
@click.option(
    "--llm-source",
    type=click.Choice(
        ["ollama", "openai_compat", "openai", "gemini", "anthropic", "huggingface"],
        case_sensitive=False,
    ),
    default="ollama",
    help=(
        "LLM provider.  "
        "'ollama' = local Ollama (Qwen/Gemma/Llama…).  "
        "'openai_compat' = any OpenAI-compatible server (LM Studio, vLLM…).  "
        "'openai' / 'gemini' / 'anthropic' = cloud providers.  "
        "'huggingface' = HuggingFace Hub."
    ),
)
@click.option("--api-token", type=str, default="", help="API token for cloud providers (not needed for local).")
@click.option("--model-name", type=str, default="qwen2.5:7b", help="Model name (e.g. qwen2.5:7b, gpt-4o, gemini-1.5-pro).")
@click.option(
    "--base-url",
    type=str,
    default="",
    help="Override API base URL (e.g. http://localhost:1234/v1 for LM Studio).",
)
def cli(switch_port, server_port, llm_source, api_token, model_name, base_url):
    app_config.fastapi_port = server_port
    app_config.llm_source = llm_source.lower()
    app_config.api_token = api_token
    app_config.switch_port = switch_port
    app_config.model_name = model_name
    app_config.base_url = base_url
    asyncio.run(main())


if __name__ == "__main__":
    cli()  # pylint: disable=no-value-for-parameter
