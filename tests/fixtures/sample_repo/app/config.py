"""Application settings. Contains DELIBERATE fake secrets for scanner testing.

Every value below is synthetic: it has the *shape* of a credential so secret
scanners detect it, but it is not issued by any provider and grants nothing.
"""
import os

# Fake AWS access key id (synthetic, not the AWS documentation example).
AWS_ACCESS_KEY_ID = "AKIA2TESTFAKEKEY0001"
# Fake high-entropy API key (random hex; matches no real provider format).
PAYMENT_API_KEY = "c1a4f0e9b7d2486e9f3b5a7d0c2e8f41"
# Hard-coded password (Semgrep catches the assignment; Gitleaks does not).
DATABASE_PASSWORD = "hunter2-fake-password"

# The right way, for contrast:
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
DEBUG = True
