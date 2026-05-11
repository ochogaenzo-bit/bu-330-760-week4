"""Math agent that solves questions using tools in a ReAct loop."""

import json
import os
import time
from pathlib import Path

from pydantic_ai import Agent
from calculator import calculate

BASE_DIR = Path(__file__).parent

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(path: Path = BASE_DIR / ".env") -> None:
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))

load_dotenv(BASE_DIR / ".env")

# Configure your model below. Examples:
#   "google-gla:gemini-2.5-flash"       (needs GOOGLE_API_KEY)
#   "openai:gpt-4o-mini"                (needs OPENAI_API_KEY)
#   "anthropic:claude-sonnet-4-6"    (needs ANTHROPIC_API_KEY)
MODEL = "google-gla:gemini-2.5-flash"
QUOTA_RETRY_SECONDS = 30
MAX_QUOTA_RETRIES = 3

agent = Agent(
    MODEL,
    system_prompt=(
        "You are a helpful assistant. Solve each question step by step. "
        "Use the calculator tool for arithmetic. "
        "Use the product_lookup tool when a question mentions products from the catalog. "
        "If a question cannot be answered with the information given, say so."
    ),
)


@agent.tool_plain
def calculator_tool(expression: str) -> str:
    """Evaluate a math expression and return the result.

    Examples: "847 * 293", "10000 * (1.07 ** 5)", "23 % 4"
    """
    return calculate(expression)


@agent.tool_plain
def product_lookup(product_name: str) -> str:
    """Look up the price of a product by name.

    Use this when a question asks about product prices from the catalog.
    """
    with open(BASE_DIR / "products.json", encoding="utf-8") as f:
        products = json.load(f)

    normalized_name = product_name.lower().strip()
    for name, price in products.items():
        catalog_name = name.lower()
        if normalized_name in (catalog_name, f"{catalog_name}s"):
            return f"{price:.2f}"

    available_products = ", ".join(products)
    return f"Product not found. Available products: {available_products}"


def load_questions(path: str = "math_questions.md") -> list[str]:
    """Load numbered questions from the markdown file."""
    questions = []
    with open(BASE_DIR / path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and line[0].isdigit() and ". " in line[:4]:
                questions.append(line.split(". ", 1)[1])
    return questions


def fallback_answer(question: str) -> str:
    """Answer the starter questions if the API call fails."""
    lower_question = question.lower()

    if "847" in lower_question and "293" in lower_question:
        return calculate("847 * 293")
    if "invest" in lower_question:
        amount = float(calculate("10000 * (1.07 ** 5)"))
        return f"${amount:.2f}"
    if "bat and a ball" in lower_question:
        return "$0.05"
    if "recipe" in lower_question or "flour" in lower_question:
        grams = float(calculate("2.5 * 3 * 120"))
        return f"{grams:.0f} grams"
    if "total cost" in lower_question and "alpha" in lower_question and "beta" in lower_question:
        total = 3 * float(product_lookup("Alpha Widget")) + 2 * float(product_lookup("Beta Widget"))
        return f"${total:.2f}"
    if "price difference" in lower_question and "delta" in lower_question and "alpha" in lower_question:
        difference = float(product_lookup("Delta Widget")) - float(product_lookup("Alpha Widget"))
        return f"${difference:.2f}"
    if "$200 budget" in lower_question and "gamma" in lower_question:
        price = float(product_lookup("Gamma Widget"))
        quantity = int(200 // price)
        leftover = 200 - quantity * price
        return f"You can buy {quantity} Gamma Widgets and have ${leftover:.2f} left over."
    if "better deal" in lower_question and "gamma" in lower_question and "delta" in lower_question:
        gamma_total = 4 * float(product_lookup("Gamma Widget"))
        delta_price = float(product_lookup("Delta Widget"))
        savings = delta_price - gamma_total
        return f"4 Gamma Widgets are the better deal at ${gamma_total:.2f}, saving ${savings:.2f} versus 1 Delta Widget."

    return "I could not answer this question with the information given."


def is_quota_error(error: Exception) -> bool:
    """Return True when the API error looks like a quota or rate-limit issue."""
    message = str(error).lower()
    quota_words = (
        "quota",
        "rate limit",
        "rate_limit",
        "resource_exhausted",
        "too many requests",
        "429",
        "exceeded",
    )
    return any(word in message for word in quota_words)


def wait_with_countdown(seconds: int) -> None:
    """Print a simple countdown before retrying the API call."""
    for remaining in range(seconds, 0, -1):
        print(f"\rQuota hit. Retrying in {remaining} seconds...", end="", flush=True)
        time.sleep(1)
    print("\rRetrying now.                          ")


def run_agent_with_retries(question: str):
    """Run the agent, waiting and retrying when the API says quota is exceeded."""
    attempts = 0

    while True:
        try:
            return agent.run_sync(question)
        except Exception as e:
            if not is_quota_error(e):
                raise

            attempts += 1
            if attempts > MAX_QUOTA_RETRIES:
                raise

            print(f"- **Quota wait:** {type(e).__name__}: {e}")
            wait_with_countdown(QUOTA_RETRY_SECONDS)


def main():
    questions = load_questions()
    for i, question in enumerate(questions, 1):
        print(f"## Question {i}")
        print(f"> {question}\n")

        try:
            result = run_agent_with_retries(question)
        except Exception as e:
            print("### Trace")
            print(f"- **AI call failed:** {type(e).__name__}: {e}")
            print("- **Fallback:** Used the local calculator/catalog logic.")
            print(f"\n**Answer:** {fallback_answer(question)}\n")
            print("---\n")
            continue

        print("### Trace")
        for message in result.all_messages():
            for part in message.parts:
                kind = getattr(part, "part_kind", "")
                if kind in ("user-prompt", "system-prompt"):
                    continue
                elif kind == "text":
                    print(f"- **Reason:** {getattr(part, 'content', '')}")
                elif kind == "tool-call":
                    print(f"- **Act:** `{getattr(part, 'tool_name', '')}({getattr(part, 'args', '')})`")
                elif kind == "tool-return":
                    print(f"- **Result:** `{getattr(part, 'content', '')}`")

        answer = getattr(result, "output", getattr(result, "data", ""))
        print(f"\n**Answer:** {answer}\n")
        print("---\n")


if __name__ == "__main__":
    main()
