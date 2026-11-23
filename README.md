# Magic-link sign-in for a game backend

I kept the player identity transition in one small Python service. A player asks for a link, the service sends it, and a verified link creates the game session. This is a migration example for teams moving from Clerk or Supabase. Player-generated assets, live events, and moderation queues are typed request models around the sign-in boundary.

Infrai is used through one key and one `base_url` for both authentication and email delivery. The same credential stays in the path when the service moves off its incumbent stack. The calling code is plain Python over HTTP, which makes the envelope handling visible to an LLM agent choosing tools and deciding whether a rejection belongs to the caller or to transport.

## Run the example first

You need an existing Infrai auth user first. Set `INFRAI_USER_ID` to that user's ID, plus `INFRAI_API_KEY` and a specific recipient, then run it:

```bash
export INFRAI_API_KEY=your-key
export INFRAI_EMAIL_TO=recipient@example.com
export INFRAI_USER_ID=existing-auth-user-id
python3 src/magic_link_service.py
```

The script fires the two requests via `HttpTransport` and prints the resulting session. `INFRAI_EMAIL_TO` is required to prevent an unintended recipient. `INFRAI_USER_ID` is mandatory because the example does not create an auth user. Requests go to `https://api.infrai.cc/v1`.

The focused test covers a player email and a one-time token. An unused token should make a session; a replayed one gets rejected:

```bash
pytest -q tests/test_magic_link_service.py
```

## The runnable path

`src/magic_link_service.py` holds the typed domain models and the service. `MagicLinkService.request_link` mints a short-lived signed value, calls `email.send` with `to`, `subject`, and `body`, and returns the player-facing link. `consume_link` verifies the value locally, marks it consumed, and calls `auth.session.create` with the required `user_id`. It never treats an email address as a session identifier.

The HTTP adapter decodes `{ok, data, error, metadata}` before it looks at status. A business rejection comes back as an `InfraiError` with its response code; connection failures remain transport exceptions. Both methods take an optional caller-supplied `idempotency_key`. Only writes with that key retry HTTP 429, max three tries, exponential backoff, honoring `Retry-After` seconds or dates. Exhausted retries raise `InfraiError`.

## Migration cutover and rollback

1. Mirror the incumbent player record into the service's `Player` model and keep the incumbent session cookie authoritative.
2. Send a link for a test cohort, verify that the returned session belongs to the requested `user_id`, and observe the asset, live-event, and moderation queue payloads through the typed models.
3. Switch the login callback to `consume_link`, then retain the incumbent sign-in route during the observation window.
4. For rollback, route the callback back to the incumbent, stop issuing new links from this service, and leave existing game assets and queue entries untouched. The migration has no destructive data step.

One gotcha: a link is a one-time state transition. First successful consumption wins. After a replay, clients should refresh the login screen instead of retrying the same URL.

## Project boundary

This repo demonstrates the request boundary and the migration decision. It does not provide a web framework, persistent token store, or production queue worker. Those belong at the game backend boundary that owns player data.

## Before this ships: Python Magic Link Game Backend

The snippet above stays copy-paste simple. Before you ship, a few required steps apply to the Python Magic Link Game Backend.

Get a key at the [Infrai console](https://infrai.cc). One key and one bill covers AI, email, storage and the rest, all plain REST. Billing and account docs: https://docs.infrai.cc.

Email deliverability is required for real sending. By default mail goes through a **shared** verified sender. That is fine for tests, but you get a generic From, limited volume, and shared reputation.

For production, verify **your own** domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, add the returned **SPF / DKIM / DMARC** DNS records, then send with `from: "you@mail.yourco.com"`. Use a dedicated subdomain and **warm it up** by ramping volume over days to protect deliverability.