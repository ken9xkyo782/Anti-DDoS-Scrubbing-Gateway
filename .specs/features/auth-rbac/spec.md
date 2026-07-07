# Auth & RBAC Specification

**Milestone:** M1 — Control-plane foundation & tenant model
**Category ID:** AUTH
**Status:** Draft (awaiting confirmation → Design)

## Problem Statement

The gateway is a multi-tenant, paid (chargeback) system where a tenant misusing another tenant's data or ranges breaks the core isolation promise (PRD 5.2). Before any service/rule/list/feed can be managed, we need authenticated sessions, two roles (`admin`, `tenant_user`), fail-closed authorization on every write, and an audit trail for every mutation (PRD 9.1, 11.2). This feature is the foundation the rest of M1–M6 build on; it also establishes two reusable primitives — the **RBAC/ownership guard** and the **audit-log writer** — consumed by all later control-plane features.

## Goals

- [ ] Authenticated session lifecycle (login, logout, expiry, current-user) for `admin` and `tenant_user`.
- [ ] Passwords stored with a modern memory-hard hash; no credential/secret ever written to logs (PRD 11.2).
- [ ] Fail-closed RBAC: role gate + tenant-ownership/CIDR-scope check on every write API; zero cross-tenant data leakage (PRD 5.2).
- [ ] Admin user management (create, edit, enable/disable, delete, reset password, assign tenant/role) — PRD 6.1.
- [ ] Audit event recorded for every user/auth mutation and every dangerous admin action (PRD 11.2).
- [ ] Reusable authz guard + audit writer that later features (services, rules, lists, feeds) plug into.

## Out of Scope

Explicitly excluded to prevent scope creep.

| Feature | Reason |
| --- | --- |
| MFA / TOTP for admin | Deferred to Backlog/GA (CM-10); likely inherited from internal SSO/IdP |
| Account lockout after N failed logins | Deferred to Backlog/GA (CM-10); failed attempts are audited but not locked in v1 |
| SSO / external IdP integration | Backlog (CM-10); v1 uses local credentials |
| Fine-grained / scoped admin permissions | GA (OP-07); v1 has one flat `admin` role (multiple admin *users* allowed) |
| Tenant/CIDR/service CRUD business logic | Separate M1 features; this spec only provides the enforcement + audit primitives they call |
| Password rotation/expiry policy | Not required by PRD for v1 |

---

## User Stories

### P1: Session login & lifecycle ⭐ MVP

**User Story**: As an `admin` or `tenant_user`, I want to log in with my username and password and receive an authenticated session, so that I can access only the dashboard/APIs my role permits.

**Why P1**: Nothing in the control-plane is reachable without authentication; it is the entry point for every other feature.

**Acceptance Criteria**:

1. WHEN a user submits a valid username + password for an `active` user whose tenant is `active` THEN the system SHALL create an authenticated session and return the user's role and tenant scope. `(AUTH-01)`
2. WHEN a user submits invalid credentials THEN the system SHALL reject with a single generic error that does not reveal whether the username exists (no user enumeration), and SHALL record a failed-login audit event. `(AUTH-02)`
3. WHEN an authenticated user logs out THEN the system SHALL terminate that session so subsequent requests with it are unauthenticated. `(AUTH-03)`
4. WHEN a session exceeds its idle timeout or absolute lifetime THEN the system SHALL treat it as expired and require re-authentication. `(AUTH-04)`
5. WHEN a request to any protected endpoint carries no valid session THEN the system SHALL reject with 401 and return no resource data. `(AUTH-05)`

**Independent Test**: Seed one admin + one tenant user; demo login (200 + role), wrong password (generic 401 + audit row), logout (session dead), expired session (401).

---

### P2: Secure credential storage ⭐ MVP

**User Story**: As the system owner, I want passwords and feed/secret credentials stored and handled securely, so that a database or log leak does not expose usable secrets.

**Why P1-critical (grouped P2 for sequencing, but blocking)**: Directly required by PRD 11.2; a security product cannot ship plaintext credentials.

**Acceptance Criteria**:

1. WHEN a user password is created or changed THEN the system SHALL store only a hash produced by a modern memory-hard algorithm (argon2id preferred; bcrypt acceptable) with per-password salt, never the plaintext. `(AUTH-06)`
2. WHEN any request, error, or job is logged THEN the system SHALL NOT emit passwords, session tokens, or feed credentials in plaintext. `(AUTH-07)`
3. WHEN a password is verified THEN the system SHALL use constant-time comparison via the hashing library, not a naive string compare. `(AUTH-08)`

**Independent Test**: Inspect DB — `password_hash` is an argon2id/bcrypt string, no plaintext column; grep application logs during login/reset — no credential material present.

---

### P1: Role-based access control (fail-closed) ⭐ MVP

**User Story**: As the system owner, I want each endpoint to enforce the caller's role, so that a `tenant_user` can never reach admin-only functions.

**Why P1**: Role separation (PRD 5.1) is meaningless unless enforced on every call.

**Acceptance Criteria**:

1. WHEN a `tenant_user` calls an admin-only endpoint (user mgmt, CIDR allocation, feed mgmt, node control, global bypass) THEN the system SHALL reject with 403 and perform no side effect. `(AUTH-09)`
2. WHEN an authorization check cannot be conclusively satisfied (missing scope, unknown resource owner, malformed context) THEN the system SHALL fail closed (deny) rather than allow. `(AUTH-10)`
3. WHEN the RBAC guard denies a request THEN the system SHALL return no partial resource data belonging to any tenant. `(AUTH-11)`

**Independent Test**: With a `tenant_user` session, call an admin endpoint → 403, no state change; force a missing-scope condition in a test → denied.

---

### P1: Tenant isolation & ownership enforcement ⭐ MVP

**User Story**: As a `tenant_user`, I want my resources fully isolated from other tenants, so that no one else can read or modify my services, lists, or telemetry — and I cannot touch theirs.

**Why P1**: Core product/commercial promise (PRD 5.2); breach undermines chargeback fairness and trust.

**Acceptance Criteria**:

1. WHEN a `tenant_user` issues a write against a resource owned by a different `tenant_id` THEN the system SHALL reject fail-closed and return no data of the other tenant. `(AUTH-12)`
2. WHEN a `tenant_user` issues a read/list request THEN the system SHALL return only resources scoped to their own `tenant_id`. `(AUTH-13)`
3. WHEN a `tenant_user` attempts to create/modify a resource on a CIDR/IP outside their `AllocatedCIDR` scope THEN the system SHALL reject (ownership + scope check). `(AUTH-14)`
4. WHEN an `admin` reads cross-tenant listings THEN the system SHALL permit it and annotate each item with its owning tenant/creator (PRD 6.1). `(AUTH-15)`

**Independent Test**: Two tenants A/B each with a resource; A's session cannot GET/PUT/DELETE B's resource (404/403, no leak); admin lists both with tenant labels. (Enforcement is provided here as a reusable guard; per-resource wiring is verified when each resource feature ships.)

---

### P1: Admin user management ⭐ MVP

**User Story**: As an `admin`, I want to create and manage users, so that I can onboard tenant staff and other admins and control their access.

**Why P1**: Required by PRD 6.1; without it only the bootstrap admin exists.

**Acceptance Criteria**:

1. WHEN an `admin` creates a user with a role and (for `tenant_user`) a `tenant_id` THEN the system SHALL persist the user with a hashed password and `status=active`, and record an audit event. `(AUTH-16)`
2. WHEN an `admin` creates a `tenant_user` without a valid `tenant_id`, or an `admin` user with a `tenant_id`, THEN the system SHALL reject with a validation error. `(AUTH-17)`
3. WHEN an `admin` edits a user's role, tenant, or status THEN the system SHALL apply the change and audit it. `(AUTH-18)`
4. WHEN an `admin` disables a user (`status=disabled`) THEN the system SHALL terminate that user's active sessions and block future logins until re-enabled. `(AUTH-19)`
5. WHEN an `admin` resets a user's password THEN the system SHALL store the new hash, invalidate that user's existing sessions, and audit the reset (without logging the new password). `(AUTH-20)`
6. WHEN an `admin` deletes a user THEN the system SHALL remove/deactivate the account, terminate its sessions, and audit the deletion. `(AUTH-21)`
7. WHEN usernames are assigned THEN the system SHALL enforce uniqueness and reject duplicates. `(AUTH-22)`

**Independent Test**: Admin creates tenant_user (audited), logs in as them; admin disables them → their session dies + login blocked; admin resets password → old session invalid, new password works.

---

### P1: Audit logging primitive ⭐ MVP

**User Story**: As an `admin`, I want every sensitive action recorded, so that changes are traceable and dangerous operations are accountable (PRD 11.2).

**Why P1**: Mandated by PRD 11.2; also the shared primitive later features (service/rule/list/feed) must call.

**Acceptance Criteria**:

1. WHEN a user is created, edited, disabled, deleted, or has a password reset THEN the system SHALL write an audit event capturing actor, action, target, timestamp, and outcome. `(AUTH-23)`
2. WHEN a login succeeds or fails, or a logout occurs THEN the system SHALL write an audit event. `(AUTH-24)`
3. WHEN a dangerous admin action is invoked (delete tenant, disable service, flush feed, activate global bypass/maintenance) THEN the system SHALL require an audit event — the guard/writer this feature exposes SHALL be the mechanism later features use. `(AUTH-25)`
4. WHEN an audit event is written THEN it SHALL NOT contain plaintext passwords, tokens, or feed credentials. `(AUTH-26)`

**Independent Test**: Perform each user mutation + login/logout; query audit log and confirm one well-formed, credential-free row per action.

---

### P1: First-admin bootstrap ⭐ MVP

**User Story**: As the operator deploying the system, I want a first admin account created deterministically, so that the very first login is possible without a chicken-and-egg problem.

**Why P1**: No admin can be created via the admin API until one exists.

**Acceptance Criteria**:

1. WHEN the system is initialized with no admin present THEN a bootstrap mechanism (seed/CLI/first-run) SHALL create exactly one `admin` user from configured credentials and audit it. `(AUTH-27)`
2. WHEN the bootstrap runs again and an admin already exists THEN it SHALL be idempotent (no duplicate, no silent password overwrite unless explicitly forced). `(AUTH-28)`
3. WHEN bootstrap credentials are supplied THEN they SHALL be taken from a secret/env source, not hardcoded, and not logged. `(AUTH-29)`

**Independent Test**: Fresh DB → run bootstrap → one admin, one audit row; run again → no change.

---

### P2: Self-service password change

**User Story**: As any authenticated user, I want to change my own password, so that I control my credential.

**Why P2**: Improves security hygiene but not required to unblock M1.

**Acceptance Criteria**:

1. WHEN a user changes their own password with a correct current password THEN the system SHALL update the hash, invalidate their *other* sessions, keep the current one, and audit it. `(AUTH-30)`
2. WHEN the current password is wrong THEN the system SHALL reject and audit the failed attempt. `(AUTH-31)`

**Independent Test**: User changes password; a second concurrent session is logged out; new password works.

---

### P2: Session inventory & basic password policy

**User Story**: As an `admin`, I want minimal session hygiene and a basic password strength floor, so that weak credentials and stale sessions are reduced.

**Why P2**: Hardening; MVP can ship with sensible defaults without the management surface.

**Acceptance Criteria**:

1. WHEN a password is set THEN the system SHALL enforce a configurable minimum length/strength floor and reject weaker values. `(AUTH-32)`
2. WHEN an admin views a user THEN the system SHALL expose `last_login_at` and active-session count (PRD 7.1 `last_login_at`). `(AUTH-33)`

**Independent Test**: Set a too-short password → rejected; admin sees `last_login_at` update after a login.

---

## Edge Cases

- WHEN a correct password is supplied for a `disabled` user OR a user whose `Tenant.status` is not `active` THEN the system SHALL deny login. `(AUTH-34)`
- WHEN an admin attempts to disable/delete the last remaining active admin THEN the system SHALL refuse (prevent total admin lockout). `(AUTH-35)`
- WHEN a tenant is deleted while it still has users THEN the system SHALL either block deletion or cascade-disable those users (no orphaned active tenant_users); the chosen rule SHALL be audited. `(AUTH-36)`
- WHEN two requests race to create users with the same username THEN the system SHALL let at most one succeed (uniqueness constraint). `(AUTH-37)`
- WHEN a session is used after its user's role/tenant changed THEN the request SHALL be evaluated against the user's current role/scope, not the value captured at login. `(AUTH-38)`
- WHEN repeated failed logins occur THEN the system SHALL audit each (lockout out of scope — CM-10) so brute-force is at least observable. `(AUTH-39)`

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| AUTH-01..05 | P1: Session login & lifecycle | Design | Pending |
| AUTH-06..08 | P1: Secure credential storage | Design | Pending |
| AUTH-09..11 | P1: RBAC fail-closed | Design | Pending |
| AUTH-12..15 | P1: Tenant isolation & ownership | Design | Pending |
| AUTH-16..22 | P1: Admin user management | Design | Pending |
| AUTH-23..26 | P1: Audit logging primitive | Design | Pending |
| AUTH-27..29 | P1: First-admin bootstrap | Design | Pending |
| AUTH-30..31 | P2: Self-service password change | - | Pending |
| AUTH-32..33 | P2: Session inventory & password policy | - | Pending |
| AUTH-34..39 | Edge cases | Design | Pending |

**ID format:** `AUTH-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 39 requirements total, 0 mapped to tasks yet (Design pending) ⚠️

---

## Success Criteria

- [ ] A cross-tenant access attempt (read or write) returns 403/404 with **zero** bytes of the other tenant's data — verified by an explicit isolation test pair.
- [ ] Every user/auth mutation and dangerous admin action produces exactly one credential-free audit row (100% coverage of the mutation set).
- [ ] `password_hash` values are argon2id/bcrypt; no plaintext credential appears in DB or logs under login/reset flows.
- [ ] Disabling a user or resetting their password terminates their active sessions within one request cycle.
- [ ] Fresh deploy reaches first admin login via bootstrap with no manual DB editing.
- [ ] The RBAC guard and audit writer are exposed as reusable components that a second feature (e.g., Tenant/CIDR CRUD) imports without modification.

---

## Decisions & Assumptions (flagged for confirmation)

1. **Session mechanism deferred to Design.** PRD says "auth/session"; because Redis is already in the stack and the spec requires prompt revocation (disable-user, reset-password, logout), **server-side sessions in Redis** are the assumed default (JWT would need a denylist to meet revocation). Requirements above are written mechanism-agnostic, so either can satisfy them — final choice in `design.md`.
2. **Multiple `admin` users allowed** in v1 (OP-07 recommendation); the *single flat admin role* is kept — fine-grained admin permissions are GA scope.
3. **Lockout/MFA/SSO excluded** per CM-10 (Backlog); failed logins are audited (observable) but not locked.
4. **Password hash:** argon2id preferred, bcrypt acceptable — exact params set in Design.
