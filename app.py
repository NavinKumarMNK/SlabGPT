from langchain.prompts import ChatPromptTemplate
from langchain.schema import StrOutputParser
from langchain.schema.runnable import Runnable
from langchain.schema.runnable.config import RunnableConfig
from langchain.callbacks.manager import CallbackManager
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain_community.llms import LlamaCpp
from langchain_community.embeddings import LlamaCppEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import MarkdownHeaderTextSplitter
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings

import yaml
import chainlit as cl
import os
import re

# yaml config loaded from config.yaml
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)
    config = config['general']

def init_data():
    print("init-data")
    headers_to_split_on = [
        ("[TIT]", "Title"),
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
    ]
    markdown_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on
    )
    chunk_size = 2500
    chunk_overlap = 2000
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    
    docs = []
    parent_dir = config['data']
    for markdown_path in os.listdir(parent_dir):
        if markdown_path.lower().endswith(".md"):
            # Read Markdown content from a file
            with open(os.path.join(parent_dir, markdown_path), "r") as file:
                markdown_text = f"[TIT] {markdown_path.replace(' - Slab.md', '')} {file.read().replace('**', '')}"
            markdown_text = re.sub(r"https?://\S+", "", markdown_text)
            markdown_text = markdown_text.replace("[]", "").replace("()", "")
            docs.extend(markdown_splitter.split_text(markdown_text))
    splits = text_splitter.split_documents(docs)
    return splits

@cl.on_chat_start
async def on_chat_start():
    print("chat-start")
    template = """Question: {question}
    Answer: Let's work this out in a step by step way to be sure we have the right answer."""

    prompt = PromptTemplate(template=template, input_variables=["question"])
    callback_manager = CallbackManager([StreamingStdOutCallbackHandler()])
    
    model_path = config['model_dir'] + "/" + config['model']
    if config['device'] == 'cpu':
        llm = LlamaCpp(
            model_path=model_path,
            temperature=0.75,
            max_tokens=2000,
            top_p=1,
            callback_manager=callback_manager,
            verbose=True,  # Verbose is required to pass to the callback manager
        )
    elif config['device'] == 'gpu':
        n_gpu_layers = 40  
        n_batch = 512  
        llm = LlamaCpp(
            model_path=model_path,
            n_gpu_layers=n_gpu_layers,
            n_batch=n_batch,
            temperature=0.75,
            max_tokens=2000,
            top_p=1,
            callback_manager=callback_manager,
            verbose=True,  # Verbose is required to pass to the callback manager
        )
    elif config['device'] == 'metal':
        n_gpu_layers = 1  # Metal set to 1 is enough.
        n_batch = 512  # Should be between 1 and n_ctx, consider the amount of RAM of your Apple Silicon Chip.
        # Make sure the model path is correct for your system!
        llm = LlamaCpp(
            model_path="/Users/rlm/Desktop/Code/llama.cpp/models/openorca-platypus2-13b.gguf.q4_0.bin",
            n_gpu_layers=n_gpu_layers,
            n_batch=n_batch,
            f16_kv=True,  # MUST set to True, otherwise you will run into problem after a couple of calls
            temperature=0.75,
            max_tokens=2000,
            top_p=1,
            callback_manager=callback_manager,
            verbose=True,  # Verbose is required to pass to the callback manager
        )
        

    template = """Answer the question based only on the following context, mention the title of the docs that the answer is coming from. Limit it to one answer.
    Context: {context}

    Question: {question}
    """
    prompt = ChatPromptTemplate.from_template(template)

    runnable = prompt | llm | StrOutputParser()
    cl.user_session.set("runnable", runnable)
    
    embedder = FastEmbedEmbeddings() # LlamaCppEmbeddings(model_path=model_path)
    cl.user_session.set("embedder", embedder)
    
    splits = init_data()
    vectordb = FAISS.from_documents(documents=splits, embedding=embedder)
    retriever = vectordb.as_retriever()
    cl.user_session.set("retriever", retriever)

    print("Ready!")

@cl.on_message
async def on_message(message: cl.Message):
    runnable = cl.user_session.get("runnable")  # type: Runnable
    embedder = cl.user_session.get("embedder")
    retriever = cl.user_session.get("retriever")

    msg = cl.Message(content="")
    async for chunk in runnable.astream({
            "question": message.content,
            "context": retriever,
        },
        config=RunnableConfig(callbacks=[cl.LangchainCallbackHandler()]),
    ):
        await msg.stream_token(chunk)

    await msg.send()