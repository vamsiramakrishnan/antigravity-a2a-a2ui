# Architecture

A secure, multi-tenant control plane for serving per-user **Antigravity skill
workspaces** from a *shared, stateless* Cloud Run service over **A2A / A2UI** on
Gemini Enterprise — without granting that shared service broad access to any
user's data.

## The problem with the obvious approach

A Cloud Run volume mount (GCS FUSE) authenticates to Cloud Storage as the **Cloud
Run service identity**, not the end user. A single Cloud Run instance also serves
many users concurrently. So if that service account can reach every personal
bucket, then a path-traversal bug, a confused-deputy, or an over-capable agent
can reach every user's data. Per-user bucket IAM does not help, because Storage
only ever sees the one shared service account. GCS FUSE additionally lacks file
locking and multi-writer semantics.

**Isolation must therefore be enforced by storage, under a per-user credential —
not by a shared runtime identity, and not by FUSE.**

## The two identity planes

Gemini Enterprise already separates `agentAuthorization` (who may invoke the
agent) from `toolAuthorizations` (credentials the agent may use downstream). This
codebase keeps those planes apart in code so they cannot be conflated:

| Plane | Question | Code |
| --- | --- | --- |
| `agentAuthorization` | Who is invoking? | `identity.verify()` → `Principal{issuer, subject}` → `derive_workspace_id` |
| `toolAuthorization` | What storage may the agent reach on the user's behalf? | `ToolCredential` → trusted `StorageAdapter` only |

Two rules fall out of this and are enforced by types:

1. **Isolation is derived from the verified OAuth `(iss, sub)` tuple**
   (`Principal`), never from an email or any string that arrives in a prompt or
   A2A message body. Email is kept only as display metadata
   (`Principal.email`, `compare=False`).
2. **The tool credential is opaque and never reaches the model.** `ToolCredential`
   redacts its secret from `repr`/equality, is handed only to the
   `StorageAdapter`, and is explicitly barred from the
   `LocalConnectionStrategy` (`assert_no_credentials`). Tokens the LLM can see
   are tokens the LLM can exfiltrate.

## Components

```
Gemini Enterprise (agentAuthorization + toolAuthorizations)
        │
        ▼
Shared A2A/A2UI Gateway (Cloud Run, stateless, NO broad GCS access)
        │                                   │
        ▼                                   ▼
Workspace Registry                    Trusted Storage Adapter
(Firestore: identity→workspace,       (per-request, workspace-scoped
 revisions, active generation)         credential — delegated or downscoped)
        │                                   │
        │                                   ▼
        │                             Cloud Storage
        │                             bucket = org × env × region
        │                               └ workspaces/{uuid}/
        │                                   ├ drafts/
        │                                   ├ revisions/sha256-…/   (immutable)
        │                                   └ activations/
        ▼
Session Materializer (download via adapter → verify content digest)
        │
        ▼
Antigravity session — LocalConnectionStrategy(skills_paths, app_data_dir)
        pinned to ONE generation, skills read-only
```

| Component | Module | Responsibility |
| --- | --- | --- |
| Identity | `identity/` | Verify agent token → `Principal`; carry opaque tool credential |
| Registry | `registry/` | identity→workspace, immutable revisions, active generation |
| Bounded tools | `registry/drafts.py` | `create_draft`/`apply_patch`/`validate`/`submit` → registry publishes |
| Storage adapter | `storage/` | Per-request, workspace-scoped object access; two isolation guards |
| Credential broker | `broker/` | Mint a workspace-scoped credential (delegated, or downscoped via CAB) |
| Materializer | `materializer/` | Download a revision into a verified, isolated local tree |
| Session | `session/` | Lifecycle + credential-free connection + generation-pinned conversation |
| Provisioner | `provisioning/` | Idempotent first-touch workspace + managed-folder IAM (own identity) |
| Gateway | `gateway/` | FastAPI: A2A agent card + invoke, Workspace REST API |

## Storage isolation

* **One bucket** per `organization × environment × region/data-residency`.
* **One managed folder** per user workspace, IAM attached to the folder. The
  shared Cloud Run service account holds **no bucket-wide object grant** —
  managed-folder IAM is additive and cannot undo a broad bucket grant.
* **Random workspace UUID**, never an email, in object names.
* **Immutable, content-addressed revisions**: `revisions/sha256-{digest}/`. The
  digest is computed over the whole skills tree (`materializer/integrity.py`);
  re-publishing identical content is idempotent and can never overwrite.
* **Activation is a pointer bump**, not a mutation: `activations/{generation}.json`.

The application layer mirrors this with two guards in `GuardedStorageAdapter`:
every key must be inside the workspace prefix (`WorkspaceLayout.contains`,
traversal-safe) **and** inside the credential's scope. A hit on either is an
`IsolationError` — a security event, because storage IAM should also have
refused. Defense in depth.

## How the gateway reaches storage

**Default — delegated user OAuth.** The request carries the user's own storage
token (`toolAuthorization`); Storage evaluates the user's IAM on their managed
folder. Storage-enforced isolation, user-attributable audit, IAM revocation, and
no central identity that can read every workspace.

**Alternative — downscoped credentials.** When delegated Storage OAuth is not
permitted, a separately-deployed **credential broker**
(`DownscopedCredentialBroker`) validates the principal→workspace binding and
mints a short-lived credential restricted by a **Credential Access Boundary** to
the workspace prefix and a minimal permission set. The broker is the privileged
component — never an agent tool, separately deployed, heavily audited. The
gateway's own `build_container` refuses to construct it, by design.

## The session lifecycle

`session/lifecycle.py` is the executable form of the design sequence:

```python
principal    = identity.verify(request)          # agentAuthorization plane
workspace    = registry.ensure_workspace(principal)
generation   = registry.resolve_active_generation(workspace.id)
credential   = request.tool_credential or broker.issue(principal, workspace.id)
storage      = storage_factory(layout, credential)        # trusted only
session      = materializer.materialize(storage, generation)  # download + verify
connection   = LocalConnectionStrategy.for_session(...)   # NO credential
conversation = conversations.create(principal, …, generation)
```

The credential exists only inside this function and the adapter it builds. It is
never returned, logged, or placed on the connection.

### Invariants

* A conversation is **pinned to one generation** for life (`Conversation.generation`).
* Activating a newer revision affects **new** conversations only; existing ones
  keep their pinned digest.
* Skills are **read-only** from the runtime (`_make_readonly`). This is hygiene,
  not a hard sandbox — executable user scripts need a separate sandboxed job.
* The model **never writes the active revision**. It works a `Draft` through the
  bounded tools; the registry performs the immutable publish.
* Materialization **verifies the content digest** after download; a mismatch
  (`IntegrityError`) refuses the session rather than running unverified bytes.

## Where GCS FUSE fits

Only two places, and never as the isolation boundary:

1. A **read-only global catalog** of org-approved skills, layered under the
   session's skills tree (`SessionConfig.global_catalog_path`).
2. A **dedicated per-user workspace runtime** whose service account can reach
   only that user's workspace.

Even there, do not use FUSE as Theia's primary mutable filesystem; use local
disk / a Git worktree for editing and publish immutable revisions to storage.

## Separation of duties

| Identity | May | May not |
| --- | --- | --- |
| Provisioner SA | create managed folder, set IAM, write metadata | serve traffic |
| Gateway SA | read workspace metadata, call broker/materializer | broad object access |
| Publisher SA | write an approved immutable revision, bump the pointer | read across workspaces |

Provisioning is idempotent and first-touch (or SCIM pre-provisioning). There is
**no** Cloud Run service per Gemini Enterprise user for the ordinary control
plane.

## Theia deployment (two modes)

* **Lightweight file-browser mode** — the A2UI frontend calls the Workspace REST
  API (`gateway/registry_api.py`): get revision → patch draft → validate →
  submit → publish/activate. No terminal, no raw bucket credential on this
  surface. This repo implements this mode.
* **Full terminal-capable Theia** — provision a dedicated **Cloud Workstation**
  per user (custom image, persistent home disk) and publish revisions through the
  Registry API. The persistent disk is the mutable editing surface; Cloud Storage
  remains the immutable revision store.

## What is implemented here vs. wired in production

Runnable today against in-memory + local-filesystem backends, with the security
boundaries fully exercised by the test suite. The production adapters
(`storage/gcs.py`, `registry/firestore.py`) are import-guarded behind the `gcp`
extra and mirror the same ports, so switching `Config.storage.backend` /
`Config.registry.backend` swaps them in without touching call sites. The
downscoped broker's STS minter and the managed-folder IAM binder are the seams an
operator supplies for the privileged identities.
