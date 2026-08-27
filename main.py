import os
import json
import sqlite3
import unicodedata
from datetime import datetime
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv
from openai import OpenAI
from tavily import TavilyClient

# ============================================================
# 1. Load dotenv config
# ============================================================

# Loads config.env from the current folder
load_dotenv("config.env")

REQUIRED_ENV_VARS = [
"OPENAI_API_KEY",
"TAVILY_API_KEY",
"OPENAI_BASE_URL",
"MEMORY_DB_PATH"
]
def validate_env() -> None:
   missing = []

    for key in REQUIRED_ENV_VARS:
if not os.getenv(key):
missing.append(key)

if missing:
raise ValueError(
"Missing required environment variables: "
+ ", ".join(missing)
+ "\nPlease check your config.env file."
)

validate_env()

# ============================================================
# 2. API clients
# ============================================================

openai_client = OpenAI(
api_key=os.getenv("OPENAI_API_KEY"),
base_url=os.getenv("OPENAI_BASE_URL")
)

tavily_client = TavilyClient(
api_key=os.getenv("TAVILY_API_KEY")
)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MEMORY_DB_PATH = os.getenv("MEMORY_DB_PATH", "udaplay_memory.db")
LOW_CONFIDENCE_THRESHOLD = float(os.getenv("LOW_CONFIDENCE_THRESHOLD", "0.75"))


# ============================================================
# 3. Internal knowledge base
# This simulates the pre-loaded UdaPlay company/game data.
# ============================================================

INTERNAL_KNOWLEDGE = {
"games": {
"fifa 21": {
"title": "FIFA 21",
"developer": "EA Vancouver and EA Romania",
"publisher": "Electronic Arts",
"release_date": "October 9, 2020",
"platforms": [
"Microsoft Windows",
"PlayStation 4",
"Xbox One",
"Nintendo Switch",
"PlayStation 5",
"Xbox Series X/S",
"Stadia"
]
},
"god of war ragnarok": {
"title": "God of War Ragnarok",
"developer": "Santa Monica Studio",
"publisher": "Sony Interactive Entertainment",
"release_date": "November 9, 2022",
"platforms": [
"PlayStation 4",
"PlayStation 5",
"Windows"
]
},
"pokemon red": {
"title": "Pokémon Red",
"developer": "Game Freak",
"publisher": "Nintendo",
"release_date": "February 27, 1996",
"platforms": [
"Game Boy"
]
}
},
"companies": {
"rockstar games": {
"name": "Rockstar Games",
"known_for": [
"Grand Theft Auto",
"Red Dead Redemption",
"Bully",
"Max Payne"
],
"current_work": None
},
"electronic arts": {
"name": "Electronic Arts",
"known_for": [
"FIFA",
"EA Sports FC",
"The Sims",
"Battlefield"
],
"current_work": None
},
"nintendo": {
"name": "Nintendo",
"known_for": [
"Mario",
"The Legend of Zelda",
"Pokémon",
"Animal Crossing"
],
"current_work": None
}
}
}


# ============================================================
# 4. Utility functions
# ============================================================

def normalize_text(text: str) -> str:
"""
Converts text to a simple searchable format.
Example:
Pokémon Red -> pokemon red
"""
text = text.lower().strip()
text = unicodedata.normalize("NFKD", text)
text = "".join(char for char in text if not unicodedata.combining(char))
return text


def detect_entity(question: str) -> Optional[str]:
"""
Finds whether the question mentions a known game or company.
"""
q = normalize_text(question)

all_entities = list(INTERNAL_KNOWLEDGE["games"].keys()) + list(
INTERNAL_KNOWLEDGE["companies"].keys()
)

for entity in all_entities:
if entity in q:
return entity

return None


def detect_intent(question: str) -> str:
"""
Detects what the user is asking for.
"""
q = normalize_text(question)

if "who developed" in q or "developer" in q or "developed" in q:
return "developer"

if "when" in q or "released" in q or "release date" in q:
return "release_date"

if "platform" in q or "launched on" in q or "available on" in q:
return "platforms"

if "working on" in q or "right now" in q or "currently" in q:
return "current_work"

return "general"


# ============================================================
# 5. Long-term memory using SQLite
# ============================================================

class LongTermMemory:
def __init__(self, db_path: str):
self.db_path = db_path
self.create_table()

def create_table(self) -> None:
with sqlite3.connect(self.db_path) as conn:
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS memory (
id INTEGER PRIMARY KEY AUTOINCREMENT,
question TEXT NOT NULL,
answer TEXT NOT NULL,
source TEXT NOT NULL,
confidence REAL NOT NULL,
entity_name TEXT,
entity_type TEXT,
raw_data TEXT,
created_at TEXT NOT NULL
)
""")

conn.commit()

def save(
self,
question: str,
answer: str,
source: str,
confidence: float,
entity_name: str = "Unknown",
entity_type: str = "Unknown",
raw_data: Optional[Dict[str, Any]] = None
) -> None:
with sqlite3.connect(self.db_path) as conn:
cursor = conn.cursor()

cursor.execute("""
INSERT INTO memory (
question,
answer,
source,
confidence,
entity_name,
entity_type,
raw_data,
created_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
""", (
question,
answer,
source,
confidence,
entity_name,
entity_type,
json.dumps(raw_data or {}),
datetime.utcnow().isoformat()
))

conn.commit()

def search(self, question: str) -> Optional[Dict[str, Any]]:
"""
Simple memory lookup.
If the exact or similar question was already asked,
return the most recent stored answer.
"""
normalized_question = normalize_text(question)

with sqlite3.connect(self.db_path) as conn:
cursor = conn.cursor()

cursor.execute("""
SELECT
question,
answer,
source,
confidence,
entity_name,
entity_type,
raw_data,
created_at
FROM memory
WHERE lower(question) LIKE ?
ORDER BY created_at DESC
LIMIT 1
""", (f"%{normalized_question}%",))

row = cursor.fetchone()

if not row:
return None

return {
"question": row[0],
"answer": row[1],
"source": row[2],
"confidence": row[3],
"entity_name": row[4],
"entity_type": row[5],
"raw_data": json.loads(row[6] or "{}"),
"created_at": row[7]
}


# ============================================================
# 6. Internal knowledge search
# ============================================================

def answer_from_internal_knowledge(question: str) -> Dict[str, Any]:
entity = detect_entity(question)
intent = detect_intent(question)

if not entity:
return {
"found": False,
"answer": None,
"confidence": 0.0,
"source": "internal_knowledge",
"reason": "No matching internal entity was found."
}

games = INTERNAL_KNOWLEDGE["games"]
companies = INTERNAL_KNOWLEDGE["companies"]

if entity in games:
game = games[entity]
title = game["title"]

if intent == "developer":
answer = f"{title} was developed by {game['developer']}."
confidence = 0.95

elif intent == "release_date":
answer = f"{title} was released on {game['release_date']}."
confidence = 0.95

elif intent == "platforms":
platforms = ", ".join(game["platforms"])
answer = f"{title} was launched or made available on: {platforms}."
confidence = 0.90

else:
platforms = ", ".join(game["platforms"])
answer = (
f"{title} was developed by {game['developer']}, "
f"published by {game['publisher']}, "
f"released on {game['release_date']}, "
f"and available on {platforms}."
)
confidence = 0.85

return {
"found": True,
"answer": answer,
"confidence": confidence,
"source": "internal_knowledge",
"entity_name": title,
"entity_type": "game",
"raw_data": game
}

if entity in companies:
company = companies[entity]
name = company["name"]

if intent == "current_work":
if company.get("current_work"):
answer = f"{name} is currently working on {company['current_work']}."
confidence = 0.90
else:
return {
"found": False,
"answer": None,
"confidence": 0.30,
"source": "internal_knowledge",
"entity_name": name,
"entity_type": "company",
"reason": "Company exists internally, but current work is unknown."
}

else:
known_for = ", ".join(company["known_for"])
answer = f"{name} is known for: {known_for}."
confidence = 0.80

return {
"found": True,
"answer": answer,
"confidence": confidence,
"source": "internal_knowledge",
"entity_name": name,
"entity_type": "company",
"raw_data": company
}

return {
"found": False,
"answer": None,
"confidence": 0.0,
"source": "internal_knowledge"
}


# ============================================================
# 7. Web search fallback using Tavily
# ============================================================

def search_web(question: str) -> Dict[str, Any]:
"""
Searches the web using Tavily if internal knowledge is missing
or confidence is low.
"""
try:
search_result = tavily_client.search(
query=question,
search_depth="advanced",
max_results=5
)

results = search_result.get("results", [])

if not results:
return {
"found": False,
"answer": None,
"confidence": 0.0,
"source": "web_search",
"raw_data": search_result
}

return {
"found": True,
"answer": None,
"confidence": 0.70,
"source": "web_search",
"raw_data": search_result
}

except Exception as error:
return {
"found": False,
"answer": None,
"confidence": 0.0,
"source": "web_search",
"error": str(error),
"raw_data": {}
}


# ============================================================
# 8. Parse web results using OpenAI
# ============================================================

def parse_web_results_with_ai(question: str, web_data: Dict[str, Any]) -> Dict[str, Any]:
"""
Uses the LLM to turn web search results into a clean answer.
"""

raw_results = web_data.get("raw_data", {}).get("results", [])

simplified_results = []

for item in raw_results:
simplified_results.append({
"title": item.get("title"),
"url": item.get("url"),
"content": item.get("content")
})

prompt = f"""
You are UdaPlay, a gaming analytics assistant.

The user asked:
{question}

Here are web search results:
{json.dumps(simplified_results, indent=2)}

Task:
1. Answer the user's question clearly.
2. Use only the information from the provided web search results.
3. If the results are uncertain, say that confidence is medium or low.
4. Return your response as JSON only.

JSON format:
{{
"answer": "clean answer here",
"confidence": 0.0,
"entity_name": "game or company name",
"entity_type": "game, company, or unknown",
"source_urls": ["url1", "url2"]
}}
"""

response = openai_client.chat.completions.create(
model=OPENAI_MODEL,
messages=[
{
"role": "system",
"content": "You are a helpful AI assistant for gaming analytics."
},
{
"role": "user",
"content": prompt
}
],
temperature=0.2
)

content = response.choices[0].message.content

try:
parsed = json.loads(content)
except json.JSONDecodeError:
parsed = {
"answer": content,
"confidence": 0.60,
"entity_name": "Unknown",
"entity_type": "unknown",
"source_urls": []
}

return {
"found": True,
"answer": parsed.get("answer", "No answer generated."),
"confidence": float(parsed.get("confidence", 0.60)),
"source": "web_search_with_ai_summary",
"entity_name": parsed.get("entity_name", "Unknown"),
"entity_type": parsed.get("entity_type", "unknown"),
"raw_data": {
"source_urls": parsed.get("source_urls", []),
"web_results": simplified_results
}
}


# ============================================================
# 9. Generate clean structured report
# ============================================================

def confidence_label(score: float) -> str:
if score >= 0.85:
return "High"
if score >= 0.60:
return "Medium"
return "Low"


def generate_report(
question: str,
result: Dict[str, Any],
path_taken: List[str],
saved_to_memory: bool
) -> str:
score = result.get("confidence", 0.0)
label = confidence_label(score)

source_urls = result.get("raw_data", {}).get("source_urls", [])

if source_urls:
sources_text = "\n".join([f"- {url}" for url in source_urls])
else:
sources_text = "No external URLs used."

report = f"""
UdaPlay Answer Report
=====================

Question:
{question}

Answer:
{result.get("answer", "No answer available.")}

Source:
{result.get("source", "unknown")}

Confidence:
{label} ({score:.2f})

Entity Name:
{result.get("entity_name", "Unknown")}

Entity Type:
{result.get("entity_type", "Unknown")}

Agent Path:
{" -> ".join(path_taken)}

Memory Status:
{"Saved to long-term memory" if saved_to_memory else "Not saved to memory"}

External Sources:
{sources_text}
"""
return report.strip()


# ============================================================
# 10. UdaPlay Agent
# ============================================================

class UdaPlayAgent:
def __init__(self):
self.memory = LongTermMemory(MEMORY_DB_PATH)

def ask(self, question: str) -> str:
path_taken = []

# Step 1: Check long-term memory
path_taken.append("long_term_memory_lookup")
memory_result = self.memory.search(question)

if memory_result and memory_result.get("confidence", 0.0) >= LOW_CONFIDENCE_THRESHOLD:
return generate_report(
question=question,
result=memory_result,
path_taken=path_taken,
saved_to_memory=False
)

# Step 2: Check internal knowledge
path_taken.append("internal_knowledge_search")
internal_result = answer_from_internal_knowledge(question)

if (
internal_result.get("found")
and internal_result.get("confidence", 0.0) >= LOW_CONFIDENCE_THRESHOLD
):
self.memory.save(
question=question,
answer=internal_result["answer"],
source=internal_result["source"],
confidence=internal_result["confidence"],
entity_name=internal_result.get("entity_name", "Unknown"),
entity_type=internal_result.get("entity_type", "Unknown"),
raw_data=internal_result.get("raw_data", {})
)

return generate_report(
question=question,
result=internal_result,
path_taken=path_taken,
saved_to_memory=True
)

# Step 3: If internal knowledge fails, search the web
path_taken.append("web_search")
web_result = search_web(question)

if not web_result.get("found"):
final_result = {
"answer": (
"I could not find a reliable answer from internal knowledge, "
"long-term memory, or web search."
),
"confidence": 0.0,
"source": "no_answer_found",
"entity_name": "Unknown",
"entity_type": "Unknown",
"raw_data": {}
}

return generate_report(
question=question,
result=final_result,
path_taken=path_taken,
saved_to_memory=False
)

# Step 4: Parse web results using AI
path_taken.append("ai_web_result_parser")
parsed_web_result = parse_web_results_with_ai(question, web_result)

# Step 5: Save web answer into long-term memory
self.memory.save(
question=question,
answer=parsed_web_result["answer"],
source=parsed_web_result["source"],
confidence=parsed_web_result["confidence"],
entity_name=parsed_web_result.get("entity_name", "Unknown"),
entity_type=parsed_web_result.get("entity_type", "Unknown"),
raw_data=parsed_web_result.get("raw_data", {})
)

return generate_report(
question=question,
result=parsed_web_result,
path_taken=path_taken,
saved_to_memory=True
)


# ============================================================
# 11. Run the agent
# ============================================================

if __name__ == "__main__":
agent = UdaPlayAgent()

print("Welcome to UdaPlay!")
print("Ask a gaming question, or type 'exit' to quit.")
print()

while True:
user_question = input("Ask UdaPlay: ")

if user_question.lower().strip() in ["exit", "quit"]:
print("Goodbye!")
break

answer = agent.ask(user_question)
print()
print(answer)
print()
print("-" * 80)
print()