"""The sandbox: simulated providers (bKash-style wallet, bank, messaging gateway, lender).

It is a separate FastAPI process with its own database and operation log. The backend talks to it
only over HTTP. Every response carries `"simulated": true`. No real money or real API is involved.
"""
