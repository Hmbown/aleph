"""
Swarm Coordination Utilities for Aleph.

This module provides pure functions for managing agent state, swarm coordination,
progress tracking, and context naming patterns. All functions return dicts for
JSON serializability.

Example usage:
    >>> from aleph.swarm.coordination import create_swarm_state, register_agent, create_agent_state
    >>> swarm = create_swarm_state("analysis-swarm")
    >>> agent = create_agent_state("explorer-1", "explorer", status="active")
    >>> swarm = register_agent(swarm, agent)
    >>> swarm = send_message(swarm, "explorer-1", "architect", "finding", {"file": "core.py"})
"""

from datetime import datetime, timezone
from typing import Any, Optional
import uuid


# =============================================================================
# AGENT STATE MANAGEMENT
# =============================================================================

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def create_agent_state(
    agent_id: str,
    agent_type: str,
    status: str = "idle"
) -> dict:
    """
    Create a new agent state dictionary.

    Args:
        agent_id: Unique identifier for the agent (e.g., "explorer-1")
        agent_type: Role/type of the agent (e.g., "explorer", "critic", "architect")
        status: Initial status - one of "idle", "active", "blocked", "completed"

    Returns:
        dict: Agent state with structure:
            {
                "agent_id": str,
                "agent_type": str,
                "status": str,
                "findings": list,
                "created_at": str (ISO format),
                "updated_at": str (ISO format),
                "metadata": dict
            }

    Example:
        >>> state = create_agent_state("explorer-1", "explorer", status="active")
        >>> state["agent_id"]
        'explorer-1'
        >>> state["status"]
        'active'
    """
    now = _utcnow_iso()
    return {
        "agent_id": agent_id,
        "agent_type": agent_type,
        "status": status,
        "findings": [],
        "created_at": now,
        "updated_at": now,
        "metadata": {}
    }


def update_agent_state(state: dict, **updates) -> dict:
    """
    Update an agent state with new values (immutable - returns new dict).

    Args:
        state: Current agent state dictionary
        **updates: Key-value pairs to update (e.g., status="active", metadata={...})

    Returns:
        dict: New agent state with updates applied and updated_at refreshed

    Example:
        >>> state = create_agent_state("agent-1", "explorer")
        >>> new_state = update_agent_state(state, status="active", metadata={"task": "scan"})
        >>> new_state["status"]
        'active'
        >>> state["status"]  # Original unchanged
        'idle'
    """
    new_state = {**state, **updates}
    new_state["updated_at"] = _utcnow_iso()
    return new_state


def add_finding(
    state: dict,
    finding: Any,
    source: Optional[str] = None
) -> dict:
    """
    Add a finding to an agent's state (immutable - returns new dict).

    Args:
        state: Current agent state dictionary
        finding: The finding to add (any JSON-serializable value)
        source: Optional source identifier (e.g., file path, context_id)

    Returns:
        dict: New agent state with finding appended to findings list

    Example:
        >>> state = create_agent_state("explorer-1", "explorer")
        >>> state = add_finding(state, {"pattern": "singleton"}, source="core.py:42")
        >>> len(state["findings"])
        1
        >>> state["findings"][0]["content"]
        {'pattern': 'singleton'}
    """
    finding_entry = {
        "id": str(uuid.uuid4())[:8],
        "content": finding,
        "source": source,
        "timestamp": _utcnow_iso()
    }
    new_findings = state["findings"] + [finding_entry]
    return update_agent_state(state, findings=new_findings)


# =============================================================================
# SWARM COORDINATION
# =============================================================================

def create_swarm_state(swarm_id: str) -> dict:
    """
    Create a new swarm state for coordinating multiple agents.

    Args:
        swarm_id: Unique identifier for the swarm (e.g., "analysis-swarm")

    Returns:
        dict: Swarm state with structure:
            {
                "swarm_id": str,
                "agents": dict (agent_id -> agent_state),
                "messages": list,
                "created_at": str (ISO format),
                "metadata": dict
            }

    Example:
        >>> swarm = create_swarm_state("my-swarm")
        >>> swarm["swarm_id"]
        'my-swarm'
        >>> len(swarm["agents"])
        0
    """
    return {
        "swarm_id": swarm_id,
        "agents": {},
        "messages": [],
        "created_at": _utcnow_iso(),
        "metadata": {}
    }


def register_agent(swarm: dict, agent_state: dict) -> dict:
    """
    Register an agent with the swarm (immutable - returns new swarm state).

    Args:
        swarm: Current swarm state dictionary
        agent_state: Agent state to register (from create_agent_state)

    Returns:
        dict: New swarm state with agent added to agents dict

    Example:
        >>> swarm = create_swarm_state("my-swarm")
        >>> agent = create_agent_state("explorer-1", "explorer")
        >>> swarm = register_agent(swarm, agent)
        >>> "explorer-1" in swarm["agents"]
        True
    """
    agent_id = agent_state["agent_id"]
    new_agents = {**swarm["agents"], agent_id: agent_state}
    return {**swarm, "agents": new_agents}


def send_message(
    swarm: dict,
    from_id: str,
    to_id: str,
    msg_type: str,
    content: Any
) -> dict:
    """
    Send a message from one agent to another within the swarm.

    Args:
        swarm: Current swarm state dictionary
        from_id: Sender agent ID
        to_id: Recipient agent ID
        msg_type: Message type (e.g., "finding", "request", "response", "status")
        content: Message content (any JSON-serializable value)

    Returns:
        dict: New swarm state with message added to messages list

    Example:
        >>> swarm = create_swarm_state("my-swarm")
        >>> swarm = send_message(swarm, "explorer-1", "architect", "finding", {"issue": "race condition"})
        >>> len(swarm["messages"])
        1
        >>> swarm["messages"][0]["msg_type"]
        'finding'
    """
    message = {
        "id": str(uuid.uuid4())[:8],
        "from_id": from_id,
        "to_id": to_id,
        "msg_type": msg_type,
        "content": content,
        "timestamp": _utcnow_iso(),
        "read": False
    }
    new_messages = swarm["messages"] + [message]
    return {**swarm, "messages": new_messages}


def broadcast_message(
    swarm: dict,
    from_id: str,
    msg_type: str,
    content: Any
) -> dict:
    """
    Broadcast a message to all agents in the swarm (except sender).

    Args:
        swarm: Current swarm state dictionary
        from_id: Sender agent ID
        msg_type: Message type (e.g., "announcement", "alert", "status")
        content: Message content (any JSON-serializable value)

    Returns:
        dict: New swarm state with broadcast message added (to_id="_broadcast_")

    Example:
        >>> swarm = create_swarm_state("my-swarm")
        >>> agent1 = create_agent_state("agent-1", "explorer")
        >>> agent2 = create_agent_state("agent-2", "explorer")
        >>> swarm = register_agent(register_agent(swarm, agent1), agent2)
        >>> swarm = broadcast_message(swarm, "agent-1", "alert", "found critical bug")
        >>> swarm["messages"][0]["to_id"]
        '_broadcast_'
    """
    message = {
        "id": str(uuid.uuid4())[:8],
        "from_id": from_id,
        "to_id": "_broadcast_",
        "msg_type": msg_type,
        "content": content,
        "timestamp": _utcnow_iso(),
        "read": False
    }
    new_messages = swarm["messages"] + [message]
    return {**swarm, "messages": new_messages}


def get_messages_for(swarm: dict, agent_id: str) -> list:
    """
    Get all messages addressed to a specific agent (including broadcasts).

    Args:
        swarm: Current swarm state dictionary
        agent_id: Agent ID to get messages for

    Returns:
        list: List of message dicts addressed to this agent or broadcast

    Example:
        >>> swarm = create_swarm_state("my-swarm")
        >>> swarm = send_message(swarm, "agent-1", "agent-2", "task", "do this")
        >>> swarm = broadcast_message(swarm, "lead", "info", "team meeting")
        >>> msgs = get_messages_for(swarm, "agent-2")
        >>> len(msgs)
        2
    """
    return [
        msg for msg in swarm["messages"]
        if msg["to_id"] == agent_id or msg["to_id"] == "_broadcast_"
    ]


def swarm_status_report(swarm: dict) -> dict:
    """
    Generate a status report for the entire swarm.

    Args:
        swarm: Current swarm state dictionary

    Returns:
        dict: Status report with structure:
            {
                "swarm_id": str,
                "agent_count": int,
                "agents_by_status": dict (status -> count),
                "agents_by_type": dict (type -> count),
                "message_count": int,
                "unread_messages": int,
                "total_findings": int,
                "created_at": str,
                "report_time": str
            }

    Example:
        >>> swarm = create_swarm_state("my-swarm")
        >>> agent = create_agent_state("explorer-1", "explorer", status="active")
        >>> agent = add_finding(agent, {"bug": "found one"})
        >>> swarm = register_agent(swarm, agent)
        >>> report = swarm_status_report(swarm)
        >>> report["agent_count"]
        1
        >>> report["total_findings"]
        1
    """
    agents = swarm["agents"].values()

    # Count by status
    status_counts = {}
    for agent in agents:
        status = agent["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    # Count by type
    type_counts = {}
    for agent in agents:
        agent_type = agent["agent_type"]
        type_counts[agent_type] = type_counts.get(agent_type, 0) + 1

    # Count findings
    total_findings = sum(len(agent["findings"]) for agent in agents)

    # Count unread messages
    unread = sum(1 for msg in swarm["messages"] if not msg["read"])

    return {
        "swarm_id": swarm["swarm_id"],
        "agent_count": len(swarm["agents"]),
        "agents_by_status": status_counts,
        "agents_by_type": type_counts,
        "message_count": len(swarm["messages"]),
        "unread_messages": unread,
        "total_findings": total_findings,
        "created_at": swarm["created_at"],
        "report_time": _utcnow_iso()
    }


# =============================================================================
# PROGRESS TRACKING
# =============================================================================

def create_progress_tracker(total_items: int) -> dict:
    """
    Create a progress tracker for monitoring task completion.

    Args:
        total_items: Total number of items to process

    Returns:
        dict: Progress tracker with structure:
            {
                "total_items": int,
                "completed": int,
                "current_item": None or str,
                "started_at": str (ISO format),
                "updated_at": str (ISO format),
                "history": list
            }

    Example:
        >>> tracker = create_progress_tracker(10)
        >>> tracker["total_items"]
        10
        >>> tracker["completed"]
        0
    """
    now = _utcnow_iso()
    return {
        "total_items": total_items,
        "completed": 0,
        "current_item": None,
        "started_at": now,
        "updated_at": now,
        "history": []
    }


def update_progress(
    tracker: dict,
    completed: int,
    current_item: Optional[str] = None
) -> dict:
    """
    Update progress tracker with new completion count (immutable).

    Args:
        tracker: Current progress tracker dictionary
        completed: New completed count
        current_item: Optional description of current item being processed

    Returns:
        dict: New tracker with updated progress

    Example:
        >>> tracker = create_progress_tracker(10)
        >>> tracker = update_progress(tracker, 3, current_item="processing file3.py")
        >>> tracker["completed"]
        3
        >>> get_progress_percentage(tracker)
        30.0
    """
    now = _utcnow_iso()
    history_entry = {
        "completed": completed,
        "current_item": current_item,
        "timestamp": now
    }
    new_history = tracker["history"] + [history_entry]
    return {
        **tracker,
        "completed": completed,
        "current_item": current_item,
        "updated_at": now,
        "history": new_history
    }


def get_progress_percentage(tracker: dict) -> float:
    """
    Calculate completion percentage from progress tracker.

    Args:
        tracker: Progress tracker dictionary

    Returns:
        float: Percentage complete (0.0 to 100.0)

    Example:
        >>> tracker = create_progress_tracker(4)
        >>> tracker = update_progress(tracker, 2)
        >>> get_progress_percentage(tracker)
        50.0
    """
    if tracker["total_items"] == 0:
        return 100.0
    return (tracker["completed"] / tracker["total_items"]) * 100.0


# =============================================================================
# CONTEXT ID PATTERNS
# =============================================================================

def agent_context_id(agent_name: str, context_type: str) -> str:
    """
    Generate a context ID for agent-specific contexts.

    Pattern: "{agent}-{type}"

    Args:
        agent_name: Name of the agent (e.g., "explorer-1")
        context_type: Type of context (e.g., "workspace", "scratch", "evidence")

    Returns:
        str: Context ID like "explorer-1-workspace"

    Example:
        >>> agent_context_id("explorer-1", "workspace")
        'explorer-1-workspace'
        >>> agent_context_id("critic", "evidence")
        'critic-evidence'
    """
    return f"{agent_name}-{context_type}"


def shared_context_id(swarm_name: str, context_type: str) -> str:
    """
    Generate a context ID for swarm-shared contexts.

    Pattern: "swarm-{name}-{type}"

    Args:
        swarm_name: Name of the swarm (e.g., "analysis")
        context_type: Type of context (e.g., "kb", "findings", "decisions")

    Returns:
        str: Context ID like "swarm-analysis-kb"

    Example:
        >>> shared_context_id("analysis", "kb")
        'swarm-analysis-kb'
        >>> shared_context_id("impl", "findings")
        'swarm-impl-findings'
    """
    return f"swarm-{swarm_name}-{context_type}"


def task_context_id(task_id: str, context_type: str) -> str:
    """
    Generate a context ID for task-specific contexts.

    Pattern: "task-{id}-{type}"

    Args:
        task_id: Identifier for the task (e.g., "core-fix", "123")
        context_type: Type of context (e.g., "spec", "findings", "code", "decision")

    Returns:
        str: Context ID like "task-core-fix-spec"

    Example:
        >>> task_context_id("core-fix", "spec")
        'task-core-fix-spec'
        >>> task_context_id("123", "findings")
        'task-123-findings'
    """
    return f"task-{task_id}-{context_type}"


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def mark_messages_read(swarm: dict, agent_id: str) -> dict:
    """
    Mark all messages for an agent as read (immutable).

    Args:
        swarm: Current swarm state dictionary
        agent_id: Agent ID whose messages to mark read

    Returns:
        dict: New swarm state with messages marked read

    Example:
        >>> swarm = create_swarm_state("my-swarm")
        >>> swarm = send_message(swarm, "a", "b", "info", "hello")
        >>> swarm["messages"][0]["read"]
        False
        >>> swarm = mark_messages_read(swarm, "b")
        >>> swarm["messages"][0]["read"]
        True
    """
    new_messages = []
    for msg in swarm["messages"]:
        if msg["to_id"] == agent_id or msg["to_id"] == "_broadcast_":
            new_messages.append({**msg, "read": True})
        else:
            new_messages.append(msg)
    return {**swarm, "messages": new_messages}


def get_agent_findings(swarm: dict, agent_id: Optional[str] = None) -> list:
    """
    Get all findings from one agent or all agents in the swarm.

    Args:
        swarm: Current swarm state dictionary
        agent_id: Optional agent ID to filter by (None = all agents)

    Returns:
        list: List of finding dicts with agent_id added to each

    Example:
        >>> swarm = create_swarm_state("my-swarm")
        >>> agent = create_agent_state("explorer-1", "explorer")
        >>> agent = add_finding(agent, {"bug": "race condition"})
        >>> swarm = register_agent(swarm, agent)
        >>> findings = get_agent_findings(swarm)
        >>> findings[0]["agent_id"]
        'explorer-1'
    """
    findings = []
    for aid, agent in swarm["agents"].items():
        if agent_id is not None and aid != agent_id:
            continue
        for finding in agent["findings"]:
            findings.append({**finding, "agent_id": aid})
    return findings


def merge_swarm_findings(swarm: dict) -> dict:
    """
    Aggregate all findings from all agents into a summary.

    Args:
        swarm: Current swarm state dictionary

    Returns:
        dict: Summary with structure:
            {
                "total_findings": int,
                "findings_by_agent": dict (agent_id -> count),
                "findings_by_type": dict (agent_type -> count),
                "all_findings": list (all findings with metadata),
                "generated_at": str
            }

    Example:
        >>> swarm = create_swarm_state("my-swarm")
        >>> agent = create_agent_state("explorer-1", "explorer")
        >>> agent = add_finding(agent, "bug 1")
        >>> agent = add_finding(agent, "bug 2")
        >>> swarm = register_agent(swarm, agent)
        >>> summary = merge_swarm_findings(swarm)
        >>> summary["total_findings"]
        2
    """
    all_findings = get_agent_findings(swarm)

    by_agent = {}
    by_type = {}

    for finding in all_findings:
        agent_id = finding["agent_id"]
        by_agent[agent_id] = by_agent.get(agent_id, 0) + 1

        agent = swarm["agents"].get(agent_id, {})
        agent_type = agent.get("agent_type", "unknown")
        by_type[agent_type] = by_type.get(agent_type, 0) + 1

    return {
        "total_findings": len(all_findings),
        "findings_by_agent": by_agent,
        "findings_by_type": by_type,
        "all_findings": all_findings,
        "generated_at": _utcnow_iso()
    }
