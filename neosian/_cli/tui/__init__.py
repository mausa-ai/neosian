"""The session view (DESIGN §35): a Textual app over one Conversation.

`neosian chat` and `neosian playground` open it on a terminal; a PROMPT,
a pipe and `--json` never do. It is a consumer of the §6 event stream
and owns no state the Conversation does not.
"""
