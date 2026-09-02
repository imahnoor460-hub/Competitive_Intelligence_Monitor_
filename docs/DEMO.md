# The public demo

A shared, read-only workspace anyone can open from the login page without
registering. It exists to show the product working on real competitors; it is
not a sandbox, and nothing in it can be changed by a visitor.

## The two flags

| Flag | Where | Set by | Means |
| --- | --- | --- | --- |
| `DEMO_USER_EMAIL` / `DEMO_USER_PASSWORD` | environment | you | which account `POST /auth/demo-login` signs in as |
| `workspaces.is_demo` | database | `scripts/provision_demo.py` | this workspace is read-only for everyone in it |

They are deliberately separate. The credentials decide *who* the demo button
signs you in as; the column decides *what is read-only*. Nothing reachable
over HTTP writes either one, so a visitor can neither mint the account nor
clear the flag.

## Sign-in

`POST /auth/demo-login` takes **no body**. The credentials live only in the
server's environment, so nothing about them reaches the browser: no email in
the markup, no password in JavaScript, no `NEXT_PUBLIC_*` variable, nothing in
an API response. The frontend's "Try the demo" button is a bare POST.

It is not a second authentication system. The endpoint resolves the configured
user, checks the configured password against the bcrypt hash on that row with
the same `verify_password` a normal login uses, and mints the same JWT through
the same `create_access_token`. Everything downstream cannot tell a demo
session from any other.

Verifying rather than trusting the environment is what makes a rotated
password fail closed: change `DEMO_USER_PASSWORD` without re-running the
provision script and the endpoint answers 503 instead of handing out sessions
against a stale hash. A deployment with neither variable set answers **404** —
an install without a demo should not advertise that the route exists.

## Read-only, enforced server-side

`dependencies.require_writable_workspace` is the single guard, sitting next to
`get_current_workspace` for the same reason tenancy does: a check scattered
across thirty routers is a check the thirty-first will forget. Every
state-changing workspace-scoped endpoint depends on it — adding or deleting
competitors and pages, running checks, generating briefings and battlecards,
approvals, integrations, budget, own-site, response library, company profiles,
traffic, category prices, member management. A demo workspace gets 403; a
normal workspace never reaches the raise.

Two endpoints carry no `workspace_id` and would otherwise be escapes, so they
are guarded on the *user* instead (`require_not_demo_user`):

* `POST /workspaces/` — otherwise a visitor creates a fresh unrestricted
  workspace and runs the paid pipeline inside it
* `DELETE /users/me` — a shared account, so this would take the demo down for
  everyone

**Keyed to the workspace, never to role.** The demo account is an `owner` in
its workspace so it can be seeded and repaired; the restriction follows the
data everyone shares, not the role of whoever is looking at it.

Delivery is blocked one level lower still, in
`delivery_service._deliver_briefings`. Both delivery paths funnel through it,
and the digest path has no request behind it — the scheduler calls
`deliver_digest` for every workspace holding approved briefings, so a
router-level guard would never see it. The briefing stays `approved` and fully
readable; only the send is suppressed, and an audit row records that.

The frontend hides these actions too — `canEdit` and `canApprove` are false
for a demo workspace, and those two booleans already gate every mutation
control — but that is presentation. The API is reachable with the demo token
directly, so `tests/test_demo_read_only.py` is the actual contract: it asserts
every mutation is refused in a demo workspace **and** that the same call still
works in a normal one.

## Setting it up

```bash
# 1. Secrets on the service
DEMO_USER_EMAIL=demo@yourdomain.com
DEMO_USER_PASSWORD=<generated>

# 2. Create the account and workspace, left open so you can seed it
cd backend && python -m scripts.provision_demo --unlock

# 3. Sign in through "Try the demo" and add the competitors through the normal
#    UI, so discovery and surface selection run exactly as they do for a real
#    workspace

# 4. Lock it
cd backend && python -m scripts.provision_demo
```

Re-run step 4 whenever the password is rotated; it re-hashes and re-asserts
the flag. The script never prints the password, not even masked.

## Known limits

* **One shared account.** Every visitor sees every other visitor's view of the
  same data. Nothing they do persists (they cannot write), but the demo is not
  private and should never hold anything sensitive.
* **Rate limits are per-workspace and in-process** (`services/rate_limiter.py`),
  so concurrent visitors share the same buckets. Read endpoints are not rate
  limited.
* **Refreshing the data is your job.** Because the demo is read-only, its
  competitors are only checked by the scheduler. If you want it to look busy,
  leave the scheduled checks running; if you want it frozen, deactivate its
  surfaces.
