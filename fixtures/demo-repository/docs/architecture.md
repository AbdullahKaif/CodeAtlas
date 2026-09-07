# TaskFlow architecture

Requests enter through `taskflow.api.routes`, which validates the session
token with `taskflow.auth.service.AuthService` and delegates to
`taskflow.services.tasks.TaskService`. The service layer is the only layer
that talks to `taskflow.db.repository`, which owns every SQL statement.

Notifications are sent by `taskflow.services.notifications` after a task is
completed.
