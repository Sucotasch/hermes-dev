# -*- coding: utf-8 -*-
"""LLM client for OpenAI-compatible servers (llama.cpp, vLLM, etc.)."""
import json
import urllib.request
import urllib.error


def chat_completion(messages, server_url="http://localhost:8888",
                    temperature=0.3, max_tokens=2000):
    """Send chat completion request to OpenAI-compatible server.

    Args:
        messages: list of {"role": "system/user/assistant", "content": "..."}
        server_url: llama.cpp server base URL
        temperature: creativity (0.0-1.0)
        max_tokens: max response tokens

    Returns:
        Assistant message content string, or None on error.
    """
    url = f"{server_url}/v1/chat/completions"
    payload = json.dumps({
        "model": "local",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        print(f"[llm] Error: {e}")
        return None


def classify_query_type(query, server_url="http://localhost:8888"):
    """Ask LLM to classify query intent.

    Returns one of: visual, technical, news, historical, comparison, general
    """
    system = """You are a search intent classifier. Given a user query, output ONLY
one word: visual, technical, news, historical, comparison, or general.

- visual: images, photos, art, galleries, portraits, design
- technical: code, API, config, error, architecture, documentation
- news: recent events, current affairs, breaking news
- historical: history, background, origins, timeline
- comparison: X vs Y, pros/cons, advantages/disadvantages
- general: everything else"""

    response = chat_completion([
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ], server_url=server_url, temperature=0.0, max_tokens=20)

    if response:
        response = response.strip().lower()
        valid = ["visual", "technical", "news", "historical", "comparison", "general"]
        if response in valid:
            return response
    return "general"


def synthesize_answer(query, evidence, query_type="general",
                      server_url="http://localhost:8888"):
    """Synthesize a final answer from evidence pack.

    Args:
        query: original user query
        evidence: compact evidence list (from _compact_evidence)
        query_type: intent label
        server_url: llama.cpp server URL

    Returns:
        Markdown string with the synthesized answer.
    """
    evidence_text = "\n".join(
        f"[{i+1}] {e.get('title', '')} ({e.get('relevance', 0):.0%})\n"
        f"    {e.get('summary', '')}\n"
        f"    Source: {e.get('url', '')}"
        for i, e in enumerate(evidence[:15])
    )

    system = f"""You are a deep research assistant. Synthesize a comprehensive answer
from the provided evidence pack. Query type: {query_type}.

Rules:
- Start with the answer, not the process
- Use inline citations [N] referencing evidence numbers
- Write in the same language as the query
- Include a Sources section at the end
- Be factual: if evidence is insufficient, say so
- For visual topics, mention image sources if available
- Format as clean Markdown"""

    response = chat_completion([
        {"role": "system", "content": system},
        {"role": "user", "content": f"Query: {query}\n\nEvidence:\n{evidence_text}"},
    ], server_url=server_url, temperature=0.3, max_tokens=3000)

    return response or "_Error: LLM synthesis failed_"
