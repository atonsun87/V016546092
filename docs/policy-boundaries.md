# Policy Boundaries

> **Purpose**: Define the policy layer that controls what agents, services, and
> external integrations can read from or write to user memory.
>
> **Core principle**: Memory is the user's private asset. Every access is
> governed by a policy, logged, and revocable.

---

## Policy Scopes

Memory nodes are tagged with a `privacy_scope` from this ordered set:

| Scope | Description | Default for Node Types |
|---|---|---|
| `PUBLIC` | Agent/service may read by default | NOTE, INSIGHT, PROJECT, TASK, VALUE (inferred) |
| `RELATIONSHIP` | Interpersonal data; shared only with companion/reflection agents | EMOTION, PART, NOTE (relational) |
| `HEALTH` | Body, mental health, somatic data | SOMA, EMOTION (clinical), BELIEF (trauma-related) |
| `PROFESSIONAL` | Work, career, financial data | PROJECT (work), TASK (work), NEED (career) |
| `PRIVATE` | Highest sensitivity; agent access requires explicit consent | Any node user marks private |

Scopes are ordered by sensitivity: `PUBLIC < RELATIONSHIP < HEALTH < PROFESSIONAL < PRIVATE`.

An agent with access to scope X can read all nodes at scope X and below (more public),
but not nodes at scopes more sensitive than X.

---

## Default Node Scope Assignment

When a node is created, its default scope is assigned by type:

| Node Type | Default Scope |
|---|---|
| `NOTE` | `PUBLIC` |
| `TASK` | `PUBLIC` |
| `PROJECT` | `PROFESSIONAL` |
| `BELIEF` | `RELATIONSHIP` |
| `VALUE` | `PUBLIC` |
| `NEED` | `RELATIONSHIP` |
| `EMOTION` | `RELATIONSHIP` |
| `PART` | `HEALTH` |
| `SOMA` | `HEALTH` |
| `THOUGHT` | `RELATIONSHIP` |
| `INSIGHT` | `PUBLIC` |
| `EVENT` | `RELATIONSHIP` |
| `PERSON` | `PRIVATE` |

The user may override any node's scope via the consent UI.

---

## Agent Permission Model

```
AgentPermission
  ├── agent_id: str
  ├── allowed_read_scopes: list[PolicyScope]
  ├── allowed_write_types: list[NodeType]
  ├── can_write: bool
  ├── max_nodes_per_request: int
  └── granted_at: str  (ISO timestamp)
```

Permissions are granted by the user. The system provides defaults per agent role
(see `docs/agent-contracts.md`), but the user can:
- **Restrict** an agent's default scope (e.g., disallow CompanionAgent from reading RELATIONSHIP)
- **Expand** a built-in agent's scope (e.g., allow PlannerAgent to read HEALTH nodes)
- **Revoke** any external agent's permission at any time

---

## Policy Engine

The `PolicyEngine` (implemented in `core/kernel/policy.py`) is the single point of
enforcement. **Every retrieval call passes through it.**

### Core operations

```python
class PolicyEngine:
    def check(self, agent_id: str, node: Node) -> bool:
        """Return True if agent_id may read node under its current permission."""

    def filter(self, agent_id: str, nodes: list[Node]) -> list[Node]:
        """Return only nodes that agent_id is allowed to read."""

    def can_write(self, agent_id: str, node_type: NodeType) -> bool:
        """Return True if agent_id is allowed to create/update nodes of this type."""

    def grant(self, agent_id: str, permission: AgentPermission) -> None:
        """Grant a new or updated permission (user-initiated)."""

    def revoke(self, agent_id: str) -> None:
        """Revoke all permissions for agent_id."""
```

### Enforcement points

1. **RetrievalRanker** — calls `policy.filter()` before returning candidates.
2. **MemoryViewBuilder** — calls `policy.filter()` when building `AgentMemoryView`.
3. **GraphAPI.apply_changes()** — calls `policy.can_write()` before creating nodes.
4. **AgentOrchestrator** — validates agent contract before dispatching.

### Audit log

Every `filter()` call logs:
```
{
  "timestamp": "...",
  "agent_id": "...",
  "requested_count": 15,
  "returned_count": 9,
  "filtered_out": 6,
  "filtered_scopes": ["HEALTH", "PRIVATE"]
}
```

Every `can_write()` rejection logs:
```
{
  "timestamp": "...",
  "agent_id": "...",
  "attempted_type": "BELIEF",
  "reason": "not_in_allowed_write_types"
}
```

---

## Write Policy

The write policy is strictly enforced by node type:

| Operation | Who may do it |
|---|---|
| Create `NOTE`, `TASK`, `THOUGHT` | Any agent with write permission |
| Create `BELIEF`, `VALUE`, `NEED` | `ReflectionAgent` only (or user directly) |
| Create `PART`, `SOMA` | `ReflectionAgent` only |
| Create `INSIGHT` | `AnalyticsAgent`, `ReflectionAgent`, or system |
| Create `PROJECT` | `PlannerAgent` or user |
| Update any node | Same agent that created it, or user |
| Delete any node | User only (soft-delete → `PRIVATE + ARCHIVED`) |
| Revise beliefs | `ReflectionAgent` (with user consent) |
| Archive memory | User or system (scheduled forgetting) |

---

## Consent Events

When an agent attempts an action in `requires_user_consent_for`, the policy engine:

1. Blocks the action and emits `consent_required` event.
2. The interface layer presents the consent request to the user.
3. User responds: `ALLOW` / `ALLOW_ONCE` / `DENY`.
4. Consent decision is logged as an immutable event.
5. If `ALLOW`: permission is updated and action proceeds.
6. If `ALLOW_ONCE`: action proceeds once, permission not updated.
7. If `DENY`: action is cancelled; agent receives `ConsentDenied` result.

---

## Data Retention Policy

| Data Type | Default Retention | User Control |
|---|---|---|
| Raw event log | Forever (immutable) | User may request full deletion |
| NOTE, TASK, PROJECT | 2 years from last access | User may delete at any time |
| EMOTION, PART, SOMA | 1 year from creation | User may delete or archive |
| BELIEF, VALUE | Forever (until revised) | User may revise or delete |
| INSIGHT | 1 year | User may delete |
| Mood snapshots | 1 year | User may export or delete |
| Agent audit log | 90 days | Not deletable (integrity) |

---

## Privacy by Default Checklist

Before shipping any new feature, verify:
- [ ] New node type has a defined default `privacy_scope`
- [ ] New agent has a defined `AgentContract` with explicit scope limits
- [ ] New retrieval path goes through `PolicyEngine.filter()`
- [ ] New write path goes through `PolicyEngine.can_write()`
- [ ] Consent is requested before sensitive scope expansion
- [ ] Audit log captures policy decisions
- [ ] Tests cover: out-of-scope access is blocked, write violation is blocked

---

## Implementation Reference

See `core/kernel/policy.py` for the Python implementation of:
- `PolicyScope` (enum)
- `AgentPermission` (dataclass)
- `PolicyEngine` (class)
- `DEFAULT_NODE_SCOPES` (dict mapping NodeType → PolicyScope)
- `PolicyViolationError` (exception)
