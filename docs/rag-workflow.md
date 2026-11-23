# BM25 rule retrieval

The demo rule flow loads candidate rules from its isolated SQLite store, filters by approval/current version, effective date and optional scope, tokenizes the query and rule fields, then scores them with `BM25Okapi`. Results include document ID, version, section, excerpt and demo source location. Intent/category constraints and evidence thresholds limit weak matches; overlapping scopes can produce `needs_review` instead of an asserted answer.

This is lexical retrieval with citations, not embeddings, a vector database, or a guarantee that generated text is factually complete. Demo policy text is explicitly fictional. Review retrieved sources before applying any real institutional policy.
