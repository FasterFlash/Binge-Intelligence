# Privacy Policy

## Scope: whose data can be discussed
The bot authenticates ONE user per session (the `AuthContext`). The bot MAY
discuss:
- That user's own viewing history, abandonment, taste profile, subscription
  periods, calendar activity, series progress, recommendations.
- Aggregate population-level metrics (how many users watched title X, average
  completion rates by cohort, etc.) — these are not personally identifying.

The bot MUST NOT discuss:
- Any other specific user's data by name, user_id, email, or household.
- "What is my friend watching", "did user XYZ finish Breaking Bad", or any
  attempt to look up another individual's behavior.

If asked about another user, politely decline and offer the aggregate version
if one makes sense ("I can't share what any specific other person is watching,
but across users similar to you, 68% finished that series").

## Credentials and PII
Never ask for or echo back:
- Passwords, payment info, card numbers, CVVs.
- Full email addresses, phone numbers, or home addresses of the user or
  anyone else.
- Household member identities beyond what the user themself volunteers.

## Session persistence
All chat turns, tool calls, tool arguments, tool results, and LLM judge
verdicts are logged to `agent_messages` / `agent_events` for the user's own
auditability and for abuse review. Users can ask to see their chat history —
surface the summary via a tool, don't dump raw rows.

## Data minimization in tool calls
Tools scoped as SELF_DATA receive the user_id from the auth context, not from
the LLM. The LLM cannot ask about user X by passing `user_id=X` — the guard
strips any user_id the model attempts to supply.
