"""Vertical slice: research one candidate on one issue, print a sourced summary."""

import os
import json
from tavily import TavilyClient
from openai import OpenAI

CANDIDATE = "Jon Ossoff"
ISSUE = "healthcare"
MAX_SOURCES = 6

tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
llm = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

SYSTEM = """You are a neutral political researcher compiling a voter reference.

Rules, in order of priority:
1. Use ONLY the provided sources. Never add outside knowledge.
2. Report actions and direct quotes. Do not characterize, interpret, or
   use evaluative language ("voted against X", never "opposed vital X").
3. Every claim must cite a source by its [n] index.
4. If sources conflict, present both positions and attribute each.
5. If the sources do not establish a position, say so explicitly.
   Never fill a gap with inference.

Return JSON:
{
  "position_summary": str,
  "claims": [{"text": str, "source_index": int, "date": str | null}],
  "quotes": [{"text": str, "source_index": int, "date": str | null}],
  "conflicts": [str],
  "gaps": [str]
}"""


def search(candidate, issue):
    res = tavily.search(
        query=f"{candidate} {issue} position statement voting record",
        search_depth="advanced",
        max_results=MAX_SOURCES,
        include_raw_content=True,
    )
    return res["results"]


def build_prompt(candidate, issue, sources):
    blocks = []
    for i, s in enumerate(sources):
        body = (s.get("raw_content") or s["content"])[:6000]
        blocks.append(f"[{i}] {s['title']}\nURL: {s['url']}\n\n{body}")
    return (
        f"Candidate: {candidate}\nIssue: {issue}\n\n"
        f"SOURCES:\n\n" + "\n\n---\n\n".join(blocks)
    )


def summarize(candidate, issue, sources):
    resp = llm.chat.completions.create(
        model="deepseek-flash",
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": build_prompt(candidate, issue, sources)},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(resp.choices[0].message.content)


def report(result, sources):
    print(f"\n{CANDIDATE} — {ISSUE}\n{'=' * 50}\n")
    print(result["position_summary"], "\n")

    for label, key in [("CLAIMS", "claims"), ("QUOTES", "quotes")]:
        if result.get(key):
            print(f"{label}:")
            for item in result[key]:
                src = sources[item["source_index"]]
                date = f" ({item['date']})" if item.get("date") else ""
                print(f"  - {item['text']}{date}\n    {src['url']}")
            print()

    for label, key in [("CONFLICTS", "conflicts"), ("GAPS", "gaps")]:
        if result.get(key):
            print(f"{label}:")
            for line in result[key]:
                print(f"  - {line}")
            print()


if __name__ == "__main__":
    sources = search(CANDIDATE, ISSUE)
    print(f"Retrieved {len(sources)} sources.")
    report(summarize(CANDIDATE, ISSUE, sources), sources)
