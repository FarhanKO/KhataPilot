"""L4 the agent: LLM providers, prompts, the bounded plan-act loop and the quarantined parser.

It has no import path to the database, the sandbox, any executor or the signing key. Its only
door to the rest of the system is `app.guard.api`.
"""
