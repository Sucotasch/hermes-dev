# -*- coding: utf-8 -*-
"""LLM client for OpenAI-compatible servers (llama.cpp, vLLM, etc.)."""
import json
import urllib.request
import urllib.error


def chat_completion(messages, server_url="http://localhost:8888",
                    temperature=0.3, max_tokens=2000, model="local"):
    """Send chat completion request to OpenAI-compatible server.

    Args:
        messages: list of {"role": "system/user/assistant", "content": "..."}
        server_url: llama.cpp server base URL
        temperature: creativity (0.0-1.0)
        max_tokens: max response tokens
        model: model name to use (default: "local" for llama.cpp)

    Returns:
        Assistant message content string, or None on error.
    """
    url = f"{server_url}/v1/chat/completions"
    payload = json.dumps({
        "model": model,
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


def classify_query_type(query, server_url="http://localhost:8888", model="local"):
    """Ask LLM to classify query intent.

    Returns one of: person, visual, technical, news, historical, comparison,
    fact, art, education, science, general
    """
    system = """You are a search intent classifier. Given a user query, output ONLY
one word from this list: person, visual, technical, news, historical, comparison,
fact, art, education, science, video, general.

- person: biography, career, filmography, aliases, personal life of a specific person
- visual: ANY query about finding/viewing images of something — photos, pics,
  pictures, image gallery, wallpapers, portraits, design. If the query mentions
  gallery/image(s)/pic(s)/photo(s)/wallpaper, prefer visual
- technical: code, API, config, error, architecture, documentation, github, download
- news: recent events, current affairs, breaking news
- historical: history of events, origins, timeline, evolution of phenomena
- comparison: X vs Y, pros/cons, advantages/disadvantages
- fact: specific factual questions (how far, how many, when exactly, what is)
- art: paintings, artists, art history, galleries, exhibitions, creative works
- education: tutorials, courses, learning, textbooks, academic
- science: physics, chemistry, biology, research, discoveries, experiments
- video: video content, clips, footage, streaming, trailers, gameplay videos
- general: everything else"""

    response = chat_completion([
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ], server_url=server_url, temperature=0.0, max_tokens=20, model=model)

    if response:
        response = response.strip().lower()
        valid = ["person", "visual", "technical", "news", "historical", "comparison",
                 "fact", "art", "education", "science", "video", "general"]
        # Models sometimes prefix/suffix extra words — match the first valid token.
        for token in response.split():
            if token in valid:
                return token
    return "general"


def enrich_query(query, query_type, server_url="http://localhost:8888", model="local"):
    """Ask LLM to enrich query with aliases, related names, context.

    For person queries: adds known aliases/real names/pseudonyms.
    Universal — LLM decides what to add from its training data.
    """
    if query_type != "person":
        return query

    system = """You are a search query enricher. The user wants to research a specific person.
Your job is to add known aliases, stage names, real names, or pseudonyms to improve search coverage.

Output ONLY the enriched query string. Include the original query plus any aliases you know.

Examples:
- Input: "Tom Cruise actor" → Output: "Tom Cruise Thomas Mapother IV actor biography"
- Input: "Eminem rapper" → Output: "Eminem Marshall Mathers Slim Shady rapper"  
- Input: "Jacqueline Lovell actress" → Output: "Jacqueline Lovell Sara St James Jackie Lovell actress biography"

If you don't know any aliases, return the original query unchanged.
Be specific: only add names you are confident about. Do not fabricate."""

    response = chat_completion([
        {"role": "system", "content": system},
        {"role": "user", "content": f"Enrich this person research query: {query}"},
    ], server_url=server_url, temperature=0.0, max_tokens=150, model=model)

    if response and len(response.strip()) > len(query):
        return response.strip()
    return query

