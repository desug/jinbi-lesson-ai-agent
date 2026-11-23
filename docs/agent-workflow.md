# Agent workflow

1. Admin authenticates with a JWT.
2. A chat request is associated with an admin-owned conversation; state is stored in SQLAlchemy models and scoped to that admin.
3. Planner returns a constrained task/subject/student reference. The offline planner is deterministic and validates the expected envelope; online planning uses the DeepSeek-compatible client.
4. If identity or subject is ambiguous, the conversation requests clarification and resumes with the selected candidate or missing parameter.
5. Server dispatches only the registered read-only tools, validates inputs, and returns structured results. Model text cannot choose arbitrary SQL or write operations.
6. Errors and diagnostics avoid echoing raw personal query content in the demo boundary.

Offline demo uses fictional records and a deterministic protocol adapter; it makes no model call. Online mode uses a locally entered key and an external API request. Rule approval operations are gated to isolated SQLite demo storage.
