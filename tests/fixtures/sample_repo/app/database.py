"""Database helpers. Contains DELIBERATE vulnerabilities for scanner testing.

Do not copy this code into real projects.
"""
import sqlite3

# Deliberately fake example credential (AWS documentation example key, not real).
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def find_user(username: str):
    """Look up a user by name. DELIBERATELY vulnerable to SQL injection."""
    connection = sqlite3.connect("users.db")
    cursor = connection.cursor()
    # VULNERABLE: user input concatenated straight into SQL.
    query = "SELECT * FROM users WHERE username = '" + username + "'"
    cursor.execute(query)
    row = cursor.fetchone()
    connection.close()
    if row is None:
        return None
    return {"username": row[0], "password": row[1]}
