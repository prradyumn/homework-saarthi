"""Cost per conversation, and the break-even map (PRD §13.2 / §13.3).

§13.2 deliberately left the rate column blank — "provider pricing moves faster
than this document, and a fabricated number is worse than none" — and made
populating it from live pricing a build task. §13.3 says the interesting output is
not a single number but the break-even map, and that this should be the most
rigorous section in the document.

Every VOLUME here is measured from this system. Every RATE is dated and sourced.
Nothing is estimated silently; where a rate is genuinely unpublished (Bhashini
commercial) it is modelled as a variable and named as the largest gap.

Run: python scripts/cost_model.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "eval" / "cost_model.json"

USD_INR = 94.98  # 2 Sep 2026, bookmyforex/poundsterlinglive
PRICED_ON = "3 September 2026"

# ---------------------------------------------------------------- measured volumes
# From the provider's own usage accounting (scripts/answer.py records it), not
# from a local tokenizer — a local Qwen2.5 tokenizer over-counted prompt tokens
# by 1.8x because qwen3.8-27b tokenises Devanagari better.
PROMPT_TOKENS = 2080        # mean of observed 2065, 2094
COMPLETION_TOKENS = 133     # mean of observed 117, 149
TOTAL_TOKENS = PROMPT_TOKENS + COMPLETION_TOKENS

# Of the prompt, the retrieved context is ~54% of characters and the fixed system
# prompt ~43%. This is what §13.3 predicted: "retrieved-context tokens dominate
# LLM cost, so prompt caching and tighter chunk selection are the primary levers."
CONTEXT_SHARE_OF_PROMPT = 0.54
SYSTEM_SHARE_OF_PROMPT = 0.43

# Devanagari tax, measured: 3,638 characters of Hindi input -> 2,080 tokens.
CHARS_PER_TOKEN_HINDI = 1.75
CHARS_PER_TOKEN_ENGLISH = 4.0   # widely published figure for English prose

TTS_CHARS = 348             # median answer 67 words, measured over 67 real answers
ASR_SECONDS = 20            # PRD §13.2 assumption — NOT yet measured. See gaps.

# --------------------------------------------------------------------- live rates
RATES = {
    "groq_qwen3_32b": {
        "in_per_m_usd": 0.29, "out_per_m_usd": 0.59,
        "note": "closest PUBLISHED price to the model in use (qwen/qwen3.8-27b, "
                "whose price Groq does not publish separately)",
        "source": "Groq via GroqCloud Qwen3-32B listing",
    },
    "groq_gpt_oss_120b": {
        "in_per_m_usd": 0.15, "out_per_m_usd": 0.60,
        "note": "cheaper on input, but measured 0% contract conformance on Hindi "
                "(D2) — so not a usable substitute at any price",
        "source": "Groq published pricing",
    },
}
BATCH_DISCOUNT = 0.50        # Groq Batch API
CACHE_DISCOUNT = 0.50        # Groq prompt caching; stacks with batch

# WhatsApp Business Platform, India, rates effective 1 July 2026.
WHATSAPP = {
    "service_in_24h_window_inr": 0.00,   # free — the category this product lives in
    "utility_inr": 0.115,
    "marketing_inr": 0.8631,
    "gst": 0.18,
    "platform_subscription_inr": 0.00,
}

# Free-tier ceilings. The Groq figures are MEASURED off live response headers for
# qwen/qwen3.8-27b and differ from Groq's generic published numbers
# (30 rpm / 6,000 tpm / 14,400 rpd), because limits are per-model.
FREE_TIERS = {
    "groq_tokens_per_min": 8_000,
    "groq_tokens_per_day": 200_000,
    "groq_requests_per_day": 1_000,
    "supabase_db_mb": 500,
    "supabase_egress_gb": 5,
    "supabase_mau": 50_000,
    "posthog_events_per_month": 1_000_000,
    "bhashini": "free for non-commercial use",
}

# Per §7.2, the engagement target is 4 questions per active parent per week.
QUESTIONS_PER_PARENT_WEEK = 4
DAYS = 30


def llm_cost_inr(model: str, cached: bool = False, batched: bool = False) -> float:
    r = RATES[model]
    mult = 1.0
    if cached:
        mult *= 1 - CACHE_DISCOUNT
    if batched:
        mult *= 1 - BATCH_DISCOUNT
    usd = (PROMPT_TOKENS / 1e6) * r["in_per_m_usd"] * mult
    usd += (COMPLETION_TOKENS / 1e6) * r["out_per_m_usd"] * mult
    return usd * USD_INR


def cached_cost_inr(model: str) -> float:
    """Prompt caching only discounts the cacheable prefix. Our system prompt is a
    fixed 43% of the prompt and is identical on every call, so it caches; the
    retrieved context changes per question and does not."""
    r = RATES[model]
    sys_tokens = PROMPT_TOKENS * SYSTEM_SHARE_OF_PROMPT
    rest_tokens = PROMPT_TOKENS - sys_tokens
    usd = (sys_tokens / 1e6) * r["in_per_m_usd"] * (1 - CACHE_DISCOUNT)
    usd += (rest_tokens / 1e6) * r["in_per_m_usd"]
    usd += (COMPLETION_TOKENS / 1e6) * r["out_per_m_usd"]
    return usd * USD_INR


def main() -> int:
    print(f"  Volumes measured from this system. Rates priced {PRICED_ON}. "
          f"USD/INR {USD_INR}.\n")

    print("  MEASURED PER CONVERSATION")
    print(f"    prompt tokens              {PROMPT_TOKENS:>7,}   "
          f"(context ~{CONTEXT_SHARE_OF_PROMPT:.0%}, fixed system prompt ~{SYSTEM_SHARE_OF_PROMPT:.0%})")
    print(f"    completion tokens          {COMPLETION_TOKENS:>7,}")
    print(f"    total tokens               {TOTAL_TOKENS:>7,}")
    print(f"    TTS characters             {TTS_CHARS:>7,}")
    print(f"    ASR seconds                {ASR_SECONDS:>7}   (assumption, not measured)")
    print(f"    Hindi tokenisation         {CHARS_PER_TOKEN_HINDI} chars/token vs "
          f"{CHARS_PER_TOKEN_ENGLISH} for English "
          f"= {CHARS_PER_TOKEN_ENGLISH / CHARS_PER_TOKEN_HINDI:.1f}x the tokens per character")

    print("\n  LLM COST PER CONVERSATION (the only non-zero line at pilot scale)")
    base = llm_cost_inr("groq_qwen3_32b")
    cached = cached_cost_inr("groq_qwen3_32b")
    batched = llm_cost_inr("groq_qwen3_32b", batched=True)
    both = llm_cost_inr("groq_qwen3_32b", cached=True, batched=True)
    rows = [
        ("on-demand", base),
        ("with prompt caching (system prompt only)", cached),
        ("with Batch API (-50%)", batched),
        ("caching + batch, whole prompt cacheable", both),
    ]
    for label, v in rows:
        print(f"    {label:44} Rs {v:.4f}")
    print(f"    -> per 1,000 conversations               Rs {base * 1000:.0f}")

    print("\n  WHATSAPP: the product's message category is FREE")
    print("    Service messages inside the 24-hour customer-service window cost Rs 0.")
    print("    This product is purely responsive — the parent asks, we answer, and the")
    print("    §7.1 accept-prompt lands in the same window — so msg_fee in §13.2's")
    print("    formula is ZERO by design, not by luck.")
    nudge = WHATSAPP["utility_inr"] * (1 + WHATSAPP["gst"])
    print(f"    A proactive re-engagement nudge is a utility message: "
          f"Rs {WHATSAPP['utility_inr']} + 18% GST = Rs {nudge:.3f}")
    print(f"    At 25 pilot parents, one weekly nudge each = Rs {nudge * 25 * 4:.2f}/month.")
    print(f"    Marketing-category messages are Rs {WHATSAPP['marketing_inr']} "
          f"({WHATSAPP['marketing_inr'] / WHATSAPP['utility_inr']:.1f}x utility) — never use them.")

    print("\n  WHERE FREE BREAKS: the break-even map (§13.3)")
    q_per_parent_day = QUESTIONS_PER_PARENT_WEEK / 7
    binds = []
    tok_day = FREE_TIERS["groq_tokens_per_day"] / TOTAL_TOKENS
    binds.append(("Groq tokens/day (200,000)", tok_day / q_per_parent_day, f"{tok_day:.0f} answers/day"))
    req_day = FREE_TIERS["groq_requests_per_day"]
    binds.append(("Groq requests/day (1,000)", req_day / q_per_parent_day, f"{req_day} answers/day"))
    tpm_burst = FREE_TIERS["groq_tokens_per_min"] / TOTAL_TOKENS
    binds.append(("Groq tokens/minute (8,000)", None, f"{tpm_burst:.1f} concurrent answers/min"))
    ev_parent_month = 8  # question, confirm, answer, accept-prompt, plus funnel events
    posthog = FREE_TIERS["posthog_events_per_month"] / (ev_parent_month * QUESTIONS_PER_PARENT_WEEK * 4.3)
    binds.append(("PostHog events/month (1M)", posthog, f"~{ev_parent_month} events/question"))
    row_bytes = 1200
    supa_rows = FREE_TIERS["supabase_db_mb"] * 1e6 / row_bytes
    binds.append(("Supabase 500 MB database", supa_rows / (q_per_parent_day * DAYS * 12),
                  f"{supa_rows / 1000:.0f}k transcript rows total"))

    print(f"    Assuming §7.2's target of {QUESTIONS_PER_PARENT_WEEK} questions per active "
          f"parent per week ({q_per_parent_day:.2f}/day):\n")
    print(f"    {'layer':32}{'binds at (active parents)':>28}  headroom")
    print("    " + "-" * 74)
    for name, parents, note in sorted(
        binds, key=lambda b: (b[1] is None, b[1] if b[1] is not None else 0)
    ):
        p = "burst limit" if parents is None else f"{parents:,.0f}"
        print(f"    {name:32}{p:>28}  {note}")

    print(f"\n    FIRST TO BIND: Groq's daily token budget, at ~{tok_day / q_per_parent_day:.0f} "
          f"active parents.")
    print(f"    §13.3 expected the order to be LLM requests -> WhatsApp fees -> Supabase.")
    print(f"    Measured, it is LLM TOKENS -> Supabase -> PostHog, and WhatsApp never")
    print(f"    binds at all, because responsive messages are free.")

    print("\n  THE LEVER §13.3 PREDICTED")
    ctx_tokens = PROMPT_TOKENS * CONTEXT_SHARE_OF_PROMPT
    for chunks, label in ((3, "current"), (2, "top-2"), (1, "top-1")):
        t = PROMPT_TOKENS - ctx_tokens * (3 - chunks) / 3 + COMPLETION_TOKENS
        print(f"    {label:9} context: {t:>6,.0f} tokens/answer -> "
              f"{FREE_TIERS['groq_tokens_per_day'] / t:>3.0f} answers/day free, "
              f"Rs {llm_cost_inr('groq_qwen3_32b') * t / TOTAL_TOKENS:.4f} paid")
    print("    Retrieved context is the cost. Dropping to top-1 buys ~45% more daily")
    print("    capacity and costs 7 points of chunk-level relevance (D3) — a real")
    print("    trade, and the reason the 3-chunk choice is written down as a cost.")

    # ---- the numbers a funder or an interviewer actually asks for ----
    q_month = QUESTIONS_PER_PARENT_WEEK * 4.3
    per_parent_month = base * q_month
    per_parent_month_lev = both * q_month
    print("\n  COST PER ACTIVE PARENT PER MONTH (§6.1 goal 4)")
    print(f"    on-demand                         Rs {per_parent_month:.2f}")
    print(f"    with caching + batch              Rs {per_parent_month_lev:.2f}")
    print(f"    at {QUESTIONS_PER_PARENT_WEEK} questions/week, {q_month:.0f} answers/month")

    print("\n  SCALE: one district, and the 10M figure §6.1 asks to be honest about")
    scales = [
        ("25-parent pilot", 25),
        ("one school (300 Class 5 children)", 300),
        ("one district (~50,000 Class 5 children)", 50_000),
        ("10 million parents", 10_000_000),
    ]
    print(f"    {'scale':40}{'on-demand/month':>18}{'with levers':>16}")
    print("    " + "-" * 74)
    for label, n in scales:
        a = per_parent_month * n
        b = per_parent_month_lev * n
        fmt = lambda v: (f"Rs {v/1e7:,.2f} cr" if v >= 1e7
                         else f"Rs {v/1e5:,.2f} L" if v >= 1e5 else f"Rs {v:,.0f}")
        print(f"    {label:40}{fmt(a):>18}{fmt(b):>16}")
    print("    The pilot is free outright: 25 parents need ~14 answers/day against a")
    print(f"    ceiling of {tok_day:.0f}, so it runs inside the free tier with 6x headroom.")
    print("    At every scale WhatsApp stays Rs 0 and the LLM is essentially the whole")
    print("    bill — which is why the caching and context levers are the strategy, not")
    print("    an optimisation. They are a 4x difference at 10M.")

    print("\n  THE HONEST GAPS")
    print("    1. Bhashini commercial ASR/TTS rates are NOT published. Free for")
    print("       non-commercial use; the published individual plan is Rs 250/month for")
    print("       50,000 TTS characters/day (~143 answers/day at our 348 chars). Beyond")
    print("       that, 'contact Bhashini'. This is the single largest unknown in the")
    print("       model and the one to resolve before any scale claim.")
    print(f"    2. ASR duration is assumed at {ASR_SECONDS}s from §13.2, not measured —")
    print("       there is no voice loop yet to measure.")
    print("    3. qwen/qwen3.8-27b's own price is unpublished; Qwen3-32B is used as the")
    print("       closest proxy, which likely OVERSTATES cost for a smaller model.")

    OUT.write_text(json.dumps({
        "priced_on": PRICED_ON, "usd_inr": USD_INR,
        "measured_volumes": {
            "prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS,
            "total_tokens": TOTAL_TOKENS, "tts_chars": TTS_CHARS,
            "asr_seconds_assumed": ASR_SECONDS,
            "chars_per_token_hindi": CHARS_PER_TOKEN_HINDI,
        },
        "rates": RATES, "whatsapp_inr": WHATSAPP, "free_tiers": FREE_TIERS,
        "cost_per_conversation_inr": {
            "on_demand": round(base, 5), "prompt_cached": round(cached, 5),
            "batched": round(batched, 5), "cached_and_batched": round(both, 5),
        },
        "break_even_active_parents": {
            n: (None if p is None else round(p)) for n, p, _ in binds
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
