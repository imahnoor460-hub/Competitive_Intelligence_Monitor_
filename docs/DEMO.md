# The public demo

A shared, read-only workspace anyone can open from the login page without
registering. It exists to show the product working on real competitors; it is
not a sandbox, and nothing in it can be changed by a visitor.

## The two flags

| Flag | Where | Set by | Means |
| --- | --- | --- | --- |
| `DEMO_USER_EMAIL` / `DEMO_USER_PASSWORD` | environment | you | which account `POST /auth/demo-login` signs in as |
| `workspaces.is_demo` | database | `scripts/provision_demo.py` | this workspace is read-only **for the demo account** |

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

That 404 is worth recognising: in an access log it looks exactly like the route
not being deployed, which is the wrong diagnosis. Two ways to tell them apart
without guessing — the response body is `{"detail":"Not found"}` where a real
routing miss says `{"detail":"Not Found"}`, and `GET /openapi.json` lists
`/auth/demo-login` whenever the code is deployed. The endpoint also logs a
warning naming the missing variable, which appears in the platform's logs
because nothing configures logging here and the root logger sits at WARNING.

## Read-only, enforced server-side

`dependencies.require_writable_workspace` is the single guard, sitting next to
`get_current_workspace` for the same reason tenancy does: a check scattered
across thirty routers is a check the thirty-first will forget. Every
state-changing workspace-scoped endpoint depends on it — adding or deleting
competitors and pages, running checks, generating briefings and battlecards,
approvals, integrations, budget, own-site, response library, company profiles,
traffic, category prices, member management.

It refuses on **two** conditions together: the workspace is the demo *and* the
caller is the shared demo account (`workspace_is_read_only_for`). Restricting
everyone in the workspace was the first design, and it locked the owner out
too — curating the demo's own data then needed a CLI unlock and re-lock every
time. An admin added with `--admin-email` edits it freely; the public session
cannot. A normal workspace never reaches the raise either way.

Two endpoints carry no `workspace_id` and would otherwise be escapes, so they
are guarded on the *user* instead (`require_not_demo_user`):

* `POST /workspaces/` — otherwise a visitor creates a fresh unrestricted
  workspace and runs the paid pipeline inside it
* `DELETE /users/me` — a shared account, so this would take the demo down for
  everyone

**Keyed to the workspace and the account, never to role.** The demo account is
an `owner` in its workspace and gets none of an owner's capabilities, while an
admin invited into the same workspace keeps all of them.

Delivery is blocked one level lower still, in
`delivery_service._deliver_briefings`. Both delivery paths funnel through it,
and the digest path has no request behind it — the scheduler calls
`deliver_digest` for every workspace holding approved briefings, so a
router-level guard would never see it. The briefing stays `approved` and fully
readable; only the send is suppressed, and an audit row records that.

The frontend hides these actions too: `canEdit` and `canApprove` gate every
mutation control already, and both are false when the workspace response says
`read_only`. That field is computed per caller — `is_demo` is a fact about the
row, `read_only` is about *you* — so an admin curating the demo keeps their own
controls. It is still only presentation. The API is reachable with the demo
token directly, so `tests/test_demo_read_only.py` is the contract: every
mutation refused for the demo session, and the same call still working for an
admin and in a normal workspace.

## Setting it up

```bash
# 1. Secrets on the service — BOTH are needed at runtime, not just at
#    provisioning time: read_only is decided by comparing the caller against
#    DEMO_USER_EMAIL, so without it the demo workspace is writable by visitors.
DEMO_USER_EMAIL=demo@yourdomain.com
DEMO_USER_PASSWORD=<generated>
DEMO_WORKSPACE_SLUG=public-demo

# 2. Create the account and workspace, and join it as yourself
cd backend && python -m scripts.provision_demo --admin-email you@yourdomain.com

# 3. Sign in as *yourself*, switch to the demo workspace, and add the
#    competitors through the normal UI, so discovery and surface selection run
#    exactly as they do for a real workspace
```

There is no unlock step: the workspace is read-only for the demo account from
the moment it is created, and writable by you throughout. `--unlock` exists for
wholesale changes — it clears the flag, which opens the workspace to the public
session too, so do not leave it in that state.

Re-run step 2 whenever the password is rotated; it re-hashes and re-asserts the
flag. The script never prints the password, not even masked.

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
