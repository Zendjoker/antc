"""The two entry points the conversation code uses. What the tools are and how they run lives with each area
(room_agent/abilities/, learning/, integrations/), registered in actions/core.py."""


def run_tool(name, args):
    """Run a tool through the action executor (actions/executor.py): it must exist, be available now, pass its schema and
    risk rules, and is checked afterwards. The result always starts with OK:, FAILED:, UNAVAILABLE:, NEEDS: or
    NEEDS_CONFIRMATION: and is the only basis for saying something happened."""
    from room_agent.actions.executor import execute

    return execute(name, args).to_model()


def active_tools():
    """Only the tools that can work right now are offered to the model (the capability registry decides)."""
    from room_agent.actions import core

    return core.offered()
