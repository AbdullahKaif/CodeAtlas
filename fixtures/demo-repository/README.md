# TaskFlow

A small task-tracking service used as the CodeAtlas demonstration repository.
It has an authentication layer, an HTTP API layer, a service layer and a
SQLite persistence layer, with tests for the first three.

**This repository contains deliberate security defects and fake credentials
for demonstration purposes.** Every secret-looking value is synthetic and
grants nothing. Do not use any of this code as a template.

## Layout

```
taskflow/
  main.py            entry point: builds the app and registers routes
  config.py          settings (with deliberately hard-coded fake secrets)
  models.py          User and Task records
  api/router.py      a minimal route registry with a decorator
  api/routes.py      HTTP handlers
  auth/service.py    login, password hashing, session tokens
  auth/tokens.py     token encoding
  services/tasks.py  task use-cases
  services/notifications.py  outbound notifications (deliberately unsafe)
  db/connection.py   SQLite connection
  db/repository.py   SQL queries (one deliberately injectable)
tests/               pytest suite
```

## Running

```
pip install -r requirements.txt
python -m taskflow.main <username> <password>
pytest
```
