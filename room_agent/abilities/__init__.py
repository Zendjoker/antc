"""What Jarvis can do, one file per area. Each file is the single place for its area:

    - the tools the model sees (name, description, parameters)
    - how each one runs (calling the implementation in room_agent/tools/)
    - its checks: state before/after, verification, undo, risk
    - the area (Group): when its tools are worth offering, its "what I can do" line, its prompt rules
    - the spoken claims it can confirm ("timer's set") and the live facts it adds to the runtime context

Adding an area = a new file here + one line in actions/core.py MODULES. Nothing else needs to change.
"""
