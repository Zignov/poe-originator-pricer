"""The pricing core behind a local HTTP boundary.

`core` assembles the model, database and pricing layers with no UI attached;
`api` serves them on 127.0.0.1 so a native overlay, another shell, or a test
can reach the same process that holds the model. Nothing in this package
imports Streamlit.
"""
