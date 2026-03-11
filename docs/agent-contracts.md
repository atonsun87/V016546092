# Agent Contracts

> **Purpose**: Define formal contracts for every agent type in SELF-OS.
> An agent contract specifies what the agent is allowed to read, write, and decide,
> and what interface it must consume (never raw storage).
>
> These contracts are the foundation of the marketplace: any external agent SDK must
> conform to an `AgentContract` before it can access user memory.

---

## Design Principle

> "Agents get views, not the whole brain."

Every agent operates on an `AgentMemoryView` — a pre-filtered, scoped snapshot
of memory assembled by the kernel using `PolicyEngine` + `RetrievalRanker`.
No agent ever receives a direct reference to `GraphStorage` or raw node lists.

---

## Contract Fields

| Field | Type | Description |
|---|---|---|
| `agent_id` | `str` | Unique stable identifier for this agent role |
| `agent_role` | `str` | Human-readable role name |
| `allowed_read_scopes` | `list[PolicyScope]` | Memory scopes this agent may read |
| `allowed_write_types` | `list[NodeType]` | Node types this agent may create or update |
| `max_nodes_per_request` | `int` | Hard cap on nodes returned per retrieval call |
| `can_proactively_act` | `bool` | Whether agent may initiate actions without user message |
| `can_revise_beliefs` | `bool` | Whether agent may propose belief revisions |
| `requires_user_consent_for` | `list[str]` | Action types requiring explicit user confirmation |

---

## Built-in Agent Contracts

### CompanionAgent

> Responds to user messages with empathy, context, and coaching.

```python
AgentContract(
    agent_id="companion",
    agent_role="Companion / Coach",
    allowed_read_scopes=[
        PolicyScope.PUBLIC,
        PolicyScope.RELATIONSHIP,
        # NOT: HEALTH, PROFESSIONAL (requires explicit consent)
    ],
    allowed_write_types=["NOTE", "EMOTION", "THOUGHT"],
    max_nodes_per_request=20,
    can_proactively_act=True,   # can send scheduled check-ins
    can_revise_beliefs=False,    # may only surface, not revise
    requires_user_consent_for=["send_notification", "update_goal"],
)
```

**What CompanionAgent sees**:
- Emotional summaries (VAD tone, dominant mood label)
- Relationship-safe memories (shared experiences, values, preferences)
- Recent NOTE and THOUGHT nodes in public scope

**What CompanionAgent cannot see**:
- Raw `HEALTH` or `PROFESSIONAL` nodes
- High-confidence vs low-confidence belief details (sees summary only)
- Full identity profile (sees distilled "how to talk to this person" summary)

---

### PlannerAgent

> Helps the user manage goals, tasks, and projects.

```python
AgentContract(
    agent_id="planner",
    agent_role="Planner / Productivity",
    allowed_read_scopes=[
        PolicyScope.PUBLIC,
        PolicyScope.PROFESSIONAL,
    ],
    allowed_write_types=["TASK", "PROJECT", "NOTE"],
    max_nodes_per_request=30,
    can_proactively_act=True,   # can schedule reminders
    can_revise_beliefs=False,
    requires_user_consent_for=["create_project", "delete_task"],
)
```

**What PlannerAgent sees**:
- Active goals and tasks
- Project nodes with PROFESSIONAL scope
- Deadline-relevant NEED and VALUE nodes

**What PlannerAgent cannot see**:
- EMOTION, PART, or SOMA nodes
- HEALTH scope data
- RELATIONSHIP scope personal memories

---

### ReflectionAgent

> Guides the user through journaling, IFS parts work, and self-inquiry.

```python
AgentContract(
    agent_id="reflection",
    agent_role="Reflection / Inner Work",
    allowed_read_scopes=[
        PolicyScope.PUBLIC,
        PolicyScope.RELATIONSHIP,
        PolicyScope.HEALTH,   # emotional / somatic data
    ],
    allowed_write_types=["BELIEF", "THOUGHT", "INSIGHT", "PART", "NEED"],
    max_nodes_per_request=15,
    can_proactively_act=False,  # only responds, never initiates
    can_revise_beliefs=True,    # core function: surface and revise beliefs
    requires_user_consent_for=["revise_belief", "archive_part"],
)
```

**What ReflectionAgent sees**:
- Full IFS parts landscape (PART nodes)
- EMOTION and SOMA nodes for body-based inquiry
- BELIEF nodes with confidence breakdown
- Low-confidence beliefs flagged for discussion

**What ReflectionAgent cannot see**:
- PROFESSIONAL scope data during personal sessions
- Raw event log (sees only synthesized memories)

---

### AnalyticsAgent

> Produces trend reports, pattern summaries, and self-knowledge dashboards.

```python
AgentContract(
    agent_id="analytics",
    agent_role="Analytics / Insights",
    allowed_read_scopes=[
        PolicyScope.PUBLIC,
        # Can be granted broader scope by user explicitly
    ],
    allowed_write_types=["INSIGHT"],   # may only create INSIGHTs
    max_nodes_per_request=100,         # needs broad access for statistics
    can_proactively_act=False,
    can_revise_beliefs=False,
    requires_user_consent_for=["share_report", "export_data"],
)
```

**What AnalyticsAgent sees**:
- Aggregated mood trends (not raw VAD values)
- Pattern summaries (not individual EMOTION nodes)
- INSIGHT nodes (its own outputs + system-generated)

**What AnalyticsAgent cannot see**:
- Individual EMOTION, PART, SOMA, or HEALTH nodes by default
- User's belief text (only counts/trends)

---

### ExternalMarketplaceAgent (template)

> Template for any third-party agent registered via the marketplace API.

```python
AgentContract(
    agent_id="<marketplace_agent_id>",
    agent_role="<user-defined>",
    allowed_read_scopes=[
        PolicyScope.PUBLIC,   # default: public scope only
        # Additional scopes require explicit user consent grant
    ],
    allowed_write_types=[],   # default: no writes
    max_nodes_per_request=10,
    can_proactively_act=False,
    can_revise_beliefs=False,
    requires_user_consent_for=["*"],  # everything requires consent
)
```

External agents start with minimum permissions and require the user to explicitly
grant additional scopes through the consent UI.

---

## Agent Memory View

Every agent receives an `AgentMemoryView`, never direct storage access:

```python
@dataclass
class AgentMemoryView:
    agent_id: str
    scope: PolicyScope
    nodes: list[Node]           # filtered by policy, ranked by retrieval scorer
    goals: list[str]            # active goal titles (public summary)
    emotional_tone: str         # "calm", "anxious", "energized", etc.
    retrieval_context: str      # why these memories were selected
    belief_summaries: list[BeliefSummary]  # confidence + status, not raw text
    built_at: str               # ISO timestamp
    node_count_filtered: int    # how many nodes were filtered out by policy
```

---

## Violation Handling

When an agent attempts to access data outside its contract:

1. `AgentContractViolation` exception is raised.
2. The violation is logged to the audit log with: `agent_id`, `attempted_scope`, `node_type`, `timestamp`.
3. If the agent is external, the API returns HTTP 403 with reason code.
4. If violations exceed threshold (e.g., 10 in 1 hour), the agent is automatically suspended pending review.

---

## Consent Model

For actions in `requires_user_consent_for`:

1. Agent generates an `IntentRequest` describing the action.
2. Kernel routes `IntentRequest` to the user via the active interface (Telegram, CLI, etc.).
3. User responds: `ALLOW`, `ALLOW_ONCE`, or `DENY`.
4. Kernel logs the consent decision with timestamp.
5. Agent proceeds or is blocked accordingly.

---

## Implementation Reference

See `core/kernel/contracts.py` for the Python implementation of:
- `AgentContract`
- `AgentMemoryView`
- `BeliefSummary`
- `AgentContractRegistry`
- `AgentContractViolation`
