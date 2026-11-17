"""DeskDora — a local-first desktop agent for Linux.

DeskDora operates your desktop for you: you type a task in plain words and
the agent reads the screen through the operating system's accessibility
tree (AT-SPI), plans the steps with a local model (Ollama) or a built-in
deterministic planner, and performs the clicking and typing itself —
asking for confirmation before anything risky and logging every action.
"""

__version__ = "3.1.0"
