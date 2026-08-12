"""
Example JARVIS plugin.
"""

from datetime import datetime

PLUGIN_INFO = {
    "name": "tell_time",
    "description": "Tells the current time and date.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "format": {"type": "STRING", "description": "Time format: 12h or 24h (default 12h)"}
        },
        "required": []
    }
}

def execute(parameters: dict, player=None, speak=None) -> str:
    fmt = parameters.get("format", "12h")
    if fmt == "24h":
        time_str = datetime.now().strftime("%H:%M")
    else:
        time_str = datetime.now().strftime("%I:%M %p")
    date_str = datetime.now().strftime("%A, %B %d, %Y")
    return f"The time is {time_str} and the date is {date_str}."