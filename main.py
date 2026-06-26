# -*- coding: utf-8 -*-
"""FloodWatch agent — LangChain 1.3 + Ollama (qwen3.5:9b)."""
import asyncio
import json
import os
import sys

from langchain_ollama import ChatOllama
from langchain.agents import create_agent

from flood_tools import (
    show_current_flood_map,
    show_historical_flood_map,
    fetch_nws_flood_alerts,
    fetch_usgs_flood_gauges,
    fetch_usgs_historical_gauges,
)

_OLLAMA_MODEL = "qwen3.5:9b"
_INPUT_MARKER = "__INPUT__:"
_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "conversation_history.json")

_SYS_PROMPT = """\
You are FloodWatch, a flood monitoring assistant that covers any US city or region.

## Tools
- show_current_flood_map(location): fetches live NWS alerts + USGS gauges and plots them.
  Use for: current conditions, active warnings, live map.
- show_historical_flood_map(location, days_back=7): fetches USGS daily peaks and plots them.
  Use for: past flooding, last week, recent surge, historical water levels.
- fetch_nws_flood_alerts(): raw NWS alert data for Dallas (text only, no map).
- fetch_usgs_flood_gauges(): raw live gauge data for Dallas (text only, no map).
- fetch_usgs_historical_gauges(days_back): raw historical data for Dallas (text only, no map).

## Rules
- For ANY map request, call show_current_flood_map(location) OR
  show_historical_flood_map(location, days_back) — these generate the map automatically.
- Extract the location from the user's message. Default to "Dallas, TX" if not specified.
- Examples: "Houston" → location="Houston, TX", "New Orleans" → location="New Orleans, LA"
- After the tool returns, give a short 2-3 sentence text summary of findings.
- Do NOT call fetch_* tools and then try to build a map manually.\
"""

_TOOLS = [
    show_current_flood_map,
    show_historical_flood_map,
    fetch_nws_flood_alerts,
    fetch_usgs_flood_gauges,
    fetch_usgs_historical_gauges,
]


def _say(text: str) -> None:
    print(text, flush=True)


def _save_history(history: list[dict]) -> None:
    with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _load_history() -> list[dict]:
    if not os.path.exists(_HISTORY_FILE):
        return []
    try:
        with open(_HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def main() -> None:
    llm = ChatOllama(model=_OLLAMA_MODEL, temperature=0, think=False)
    agent = create_agent(llm, tools=_TOOLS, system_prompt=_SYS_PROMPT)

    history = _load_history()

    _say(f"FloodWatch Agent ready  (model: {_OLLAMA_MODEL}, framework: LangChain {__import__('langchain').__version__})")
    if history:
        _say(f"Restored {len(history)} messages from previous session.")
    else:
        _say("Starting fresh session.")
    _say("Try: 'show me current flood conditions in Houston' or 'Dallas floods last week'")
    _say("Commands: 'clear history' to reset  |  'exit' to quit")

    loop = asyncio.get_event_loop()

    while True:
        _say(_INPUT_MARKER)
        # Read stdin in a thread so we don't block the event loop
        user_input = await loop.run_in_executor(None, sys.stdin.readline)
        user_input = user_input.strip()

        if not user_input:
            break  # EOF — subprocess stdin was closed

        if user_input.lower() == "exit":
            _say("Goodbye.")
            break

        if user_input.lower() == "clear history":
            history = []
            _save_history(history)
            _say("Conversation history cleared.")
            continue

        # Build messages list: history + new user message
        messages = []
        for msg in history:
            if msg["role"] == "human":
                from langchain_core.messages import HumanMessage
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "ai":
                from langchain_core.messages import AIMessage
                messages.append(AIMessage(content=msg["content"]))
        from langchain_core.messages import HumanMessage as HM
        messages.append(HM(content=user_input))

        result = await agent.ainvoke({"messages": messages})

        # LangGraph returns {"messages": [...]} — last message is the AI reply
        output = ""
        if isinstance(result, dict) and "messages" in result:
            last = result["messages"][-1]
            output = last.content if hasattr(last, "content") else str(last)
        else:
            output = str(result)

        _say(output)

        history.append({"role": "human", "content": user_input})
        history.append({"role": "ai", "content": output})
        _save_history(history)


if __name__ == "__main__":
    asyncio.run(main())
