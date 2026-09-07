"""Application settings.

DELIBERATE DEFECT: secrets are hard-coded below. Every value is synthetic - it
has the shape of a credential so that scanners detect it, but it was never
issued by any provider and grants nothing.
"""
import os

DATABASE_PATH = os.environ.get("TASKFLOW_DB", "taskflow.sqlite3")

# Fake signing key for session tokens (synthetic hex, not a real key).
SECRET_KEY = "9f2b7c41e8d04a6f93b1c5e7d2a8f6034c1e9b7d5a3f2e1c8b6d4a2f0e9c7b5a"
# Fake AWS access key id (synthetic; not the AWS documentation example).
AWS_ACCESS_KEY_ID = "AKIA2DEMOFAKEKEY0042"
# Fake payment provider key (test-mode shape, random characters).
PAYMENT_API_KEY = "sk_test_51DemoFakeKey0000000000000000abcdEF"
# Hard-coded admin password.
ADMIN_PASSWORD = "taskflow-admin-demo"

# The right way, for contrast: read from the environment.
SMTP_PASSWORD = os.environ.get("TASKFLOW_SMTP_PASSWORD", "")
TOKEN_TTL_SECONDS = 3600
