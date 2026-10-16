"""LLM providers behind one interface: `anthropic` (Claude), `openai_compatible`, and a deterministic
`mock` that needs no key and no network (tests, CI and the red-team suite use it)."""
