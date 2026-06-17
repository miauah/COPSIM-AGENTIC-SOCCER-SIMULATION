#!/usr/bin/env python3
"""
soccer_harness.py — LLM ↔ CoppeliaSim Soccer Harness (Tkinter GUI)
===================================================================
Bridges a local LLM (via LM Studio OpenAI-compatible API) with the
CoppeliaSim soccer robot controlled via llm_soccer_agent.py subprocess calls.

Usage:
    python soccer_harness.py
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox, font as tkfont
import subprocess
import threading
import json
import re
import os
import sys
import time
import requests

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_SCRIPT = os.path.join(SCRIPT_DIR, "llm_soccer_agent.py")
DEFAULT_API_URL = "http://localhost:1234/v1"
DEFAULT_MAX_STEPS = 30
SUBPROCESS_TIMEOUT = 30  # seconds
LLM_TIMEOUT = 60  # seconds

# ─────────────────────────────────────────────────────────────────────────────
# System Prompt
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_SYSTEM_PROMPT = """\
You are a robot controller AI for a CoppeliaSim simulation. You control a PioneerP3DX robot.
You can handle ANY movement task: going to coordinates, approaching objects, kicking balls, spinning, etc.

## Available Commands (respond with ONE JSON object per turn)
- {"command": "move_forward", "distance": <meters>}  — drive forward
- {"command": "move_backward", "distance": <meters>}  — drive backward
- {"command": "rotate", "angle": <degrees>}  — positive=left(counter-clockwise), negative=right(clockwise)
   (WARNING: YOU MUST USE 'angle' in DEGREES. DO NOT USE RADIANS. Do NOT output 'angle_rad'.)
- {"command": "kick"}  — kick the ball (auto push+retract, no parameters needed)
- {"command": "calc_nav", "x": <float>, "y": <float>}  — get heading error and distance to any point
- {"command": "get_data"}  — request fresh sensor data
- {"command": "celebrate"}  — victory spin
- {"command": "DONE", "reason": "<why>"}  — task complete

## How Sensor Data Works
After each command you receive pre-calculated data. NO MATH NEEDED. Key fields:
- heading_error_to_ball_deg: degrees you need to rotate to face the ball (negative = turn right)
- strike_position: the ideal spot to stand before kicking, with heading_error_deg and distance
- kick_aligned: true means you are in position and aligned to kick. Just call kick!
- kick_opportunity: true means the ball is directly in front of you and close enough to kick
- goal_scored: true means the ball is in the goal box

## Recipes (follow these step by step)

### To score a goal:
1. Read `strike_position.suggestion` from sensor data.
2. If it tells you to rotate, rotate by that amount. If it tells you to move_forward, move by that amount.
3. Re-check get_data. When `kick_aligned` is true, or the suggestion says "You are in position!", call `kick`.
4. Check if `goal_scored` is true. If yes, celebrate and then send DONE.
5. If missed, get_data again and start from step 1

### To go to a coordinate (x, y):
1. Call calc_nav with the target x, y
2. Read the `distance` from the result. 
   **STOPPING CONDITION**: If distance < 0.3m, you have arrived! Send `{"command": "DONE", "reason": "arrived at target"}`.
3. If not arrived, read the `suggestion` and follow it (rotate, then move forward).

### To move to the ball:
1. Read distances.robot_to_ball from sensor data.
   **STOPPING CONDITION**: If distance < 0.6m, you are right next to it! Send `{"command": "DONE", "reason": "arrived at ball"}`.
2. If not arrived, read heading_error_to_ball_deg. Rotate by that amount.
3. Move forward. 

### To spin in place:
1. Rotate by the desired angle (e.g. 360 for a full spin), then send DONE.

## Rules
- **ADAPTIVE SPEED**: When moving forward, be aggressive if far, careful if close:
  - If distance > 1.0m, move_forward `0.6`
  - If distance > 0.4m, move_forward `0.3`
  - If distance <= 0.4m, move_forward `0.1` (to prevent bumping into things!)
- **TOLERANCE (DO NOT OVER-ADJUST)**: 
  - If an angle error is between -5 and 5 degrees, you are pointing perfectly! DO NOT rotate. Proceed to move forward.
  - Once you reach a target (distance < 0.3m for coords, < 0.6m for ball), DO NOT keep adjusting. Stop and send DONE.
- Always include a "thinking" field in your JSON explaining your reasoning.
- ALL ANGLES ARE IN DEGREES. DO NOT USE RADIANS.

## Response Format
Respond ONLY with valid JSON. No markdown, no explanation outside JSON. Example:
{"thinking": "Ball is 0.5m ahead, heading error is -4.6 deg. Close enough to drive forward.", "command": "move_forward", "distance": 0.2}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Run llm_soccer_agent.py subprocess
# ─────────────────────────────────────────────────────────────────────────────
def run_agent_command(*args):
    """Run llm_soccer_agent.py with the given arguments and return parsed JSON."""
    cmd = [sys.executable, AGENT_SCRIPT] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            cwd=SCRIPT_DIR,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            return {"error": f"Process exited with code {result.returncode}. stderr: {stderr}"}

        if stdout:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return {"raw_output": stdout}
        return {"status": "Command completed (no output)"}

    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {SUBPROCESS_TIMEOUT}s"}
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Extract JSON from LLM response (handles markdown fences, etc.)
# ─────────────────────────────────────────────────────────────────────────────
def extract_json_from_response(text):
    """Try to parse JSON from LLM output, handling common quirks."""
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding a JSON object anywhere in the text
    brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Call LM Studio API
# ─────────────────────────────────────────────────────────────────────────────
def call_llm(api_url, messages, model="qwen3.5-4b"):
    """Send messages to the LM Studio OpenAI-compatible API."""
    url = f"{api_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 512,
        "stream": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content, None
    except requests.exceptions.ConnectionError:
        return None, "Cannot connect to LM Studio API. Is it running?"
    except requests.exceptions.Timeout:
        return None, f"LLM API timed out after {LLM_TIMEOUT}s"
    except Exception as e:
        return None, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Build a command list from parsed LLM JSON
# ─────────────────────────────────────────────────────────────────────────────
def build_subprocess_args(parsed):
    """Convert parsed LLM JSON command into subprocess argument list."""
    cmd = parsed.get("command", "").lower().strip()

    if cmd == "move_forward":
        dist = parsed.get("distance", 0.2)
        return ["move_forward", str(dist)]
    elif cmd == "move_backward":
        dist = parsed.get("distance", 0.2)
        return ["move_backward", str(dist)]
    elif cmd == "rotate":
        if "angle" in parsed:
            angle = parsed["angle"]
        elif "angle_deg" in parsed:
            angle = parsed["angle_deg"]
        elif "angle_rad" in parsed:
            angle = parsed["angle_rad"] * 57.2958
        else:
            angle = 0
        return ["rotate", str(angle)]
    elif cmd == "move_pusher":
        vel = parsed.get("velocity", 4.0)
        pos = parsed.get("position", 0.25)
        return ["move_pusher", str(vel), str(pos)]
    elif cmd == "kick":
        return ["kick"]
    elif cmd == "calc_nav":
        x = parsed.get("x", 0)
        y = parsed.get("y", 0)
        return ["calc_nav", str(x), str(y)]
    elif cmd == "get_data":
        return ["get_data"]
    elif cmd == "celebrate":
        return ["celebrate"]
    elif cmd == "done":
        return None  # Signal to stop the loop
    else:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Tkinter Application
# ═══════════════════════════════════════════════════════════════════════════════
class SoccerHarnessApp:
    def __init__(self, root):
        self.root = root
        self.root.title("⚽ LLM Soccer Harness")
        self.root.geometry("980x750")
        self.root.minsize(800, 600)
        self.root.configure(bg="#1e1e2e")

        # State
        self.running_loop = False
        self.stop_event = threading.Event()
        self.conversation_history = []
        self.loop_thread = None

        # Fonts
        self.font_mono = tkfont.Font(family="Consolas", size=10)
        self.font_label = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.font_title = tkfont.Font(family="Segoe UI", size=14, weight="bold")
        self.font_btn = tkfont.Font(family="Segoe UI", size=10)
        self.font_input = tkfont.Font(family="Segoe UI", size=11)

        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────
    # UI Construction
    # ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Colors ──
        bg = "#1e1e2e"
        panel_bg = "#282a36"
        fg = "#f8f8f2"
        accent = "#bd93f9"
        accent2 = "#50fa7b"
        red = "#ff5555"
        orange = "#ffb86c"
        cyan = "#8be9fd"
        comment = "#6272a4"
        yellow = "#f1fa8c"

        # ══════════════════════════════════════════════════════════════════
        # Top bar
        # ══════════════════════════════════════════════════════════════════
        top_frame = tk.Frame(self.root, bg=bg, pady=6)
        top_frame.pack(fill=tk.X)

        tk.Label(
            top_frame, text="⚽ LLM Soccer Harness", font=self.font_title,
            bg=bg, fg=accent
        ).pack(side=tk.LEFT, padx=12)

        # API URL
        url_frame = tk.Frame(top_frame, bg=bg)
        url_frame.pack(side=tk.RIGHT, padx=12)
        tk.Label(url_frame, text="LM Studio API:", font=self.font_label, bg=bg, fg=comment).pack(side=tk.LEFT)
        self.api_url_var = tk.StringVar(value=DEFAULT_API_URL)
        api_entry = tk.Entry(
            url_frame, textvariable=self.api_url_var, width=30,
            font=self.font_mono, bg=panel_bg, fg=fg,
            insertbackground=fg, relief=tk.FLAT, highlightthickness=1,
            highlightcolor=accent, highlightbackground=comment
        )
        api_entry.pack(side=tk.LEFT, padx=4)

        # ══════════════════════════════════════════════════════════════════
        # Main body: left panel (controls) + right panel (chat)
        # ══════════════════════════════════════════════════════════════════
        body = tk.Frame(self.root, bg=bg)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # ── Left sidebar ──
        sidebar = tk.Frame(body, bg=panel_bg, width=200, padx=8, pady=8,
                           highlightthickness=1, highlightbackground=comment)
        sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="Controls", font=self.font_label, bg=panel_bg, fg=accent).pack(pady=(0, 8))

        # Start / Stop Sim
        self.btn_start_sim = tk.Button(
            sidebar, text="▶  Start Sim", font=self.font_btn,
            bg="#44475a", fg=accent2, activebackground="#6272a4", activeforeground=fg,
            relief=tk.FLAT, cursor="hand2", command=self._on_start_sim
        )
        self.btn_start_sim.pack(fill=tk.X, pady=2)

        self.btn_stop_sim = tk.Button(
            sidebar, text="⏹  Stop Sim", font=self.font_btn,
            bg="#44475a", fg=orange, activebackground="#6272a4", activeforeground=fg,
            relief=tk.FLAT, cursor="hand2", command=self._on_stop_sim
        )
        self.btn_stop_sim.pack(fill=tk.X, pady=2)

        tk.Frame(sidebar, height=1, bg=comment).pack(fill=tk.X, pady=8)

        self.btn_get_data = tk.Button(
            sidebar, text="📡  Get Data", font=self.font_btn,
            bg="#44475a", fg=cyan, activebackground="#6272a4", activeforeground=fg,
            relief=tk.FLAT, cursor="hand2", command=self._on_get_data
        )
        self.btn_get_data.pack(fill=tk.X, pady=2)

        tk.Frame(sidebar, height=1, bg=comment).pack(fill=tk.X, pady=8)

        # Emergency Stop
        self.btn_emergency = tk.Button(
            sidebar, text="🛑  EMERGENCY STOP", font=self.font_btn,
            bg=red, fg="#ffffff", activebackground="#cc3333", activeforeground="#ffffff",
            relief=tk.FLAT, cursor="hand2", command=self._on_emergency_stop
        )
        self.btn_emergency.pack(fill=tk.X, pady=2)

        tk.Frame(sidebar, height=1, bg=comment).pack(fill=tk.X, pady=8)

        # Max Steps
        tk.Label(sidebar, text="Max Steps:", font=self.font_label, bg=panel_bg, fg=comment).pack(anchor=tk.W)
        self.max_steps_var = tk.IntVar(value=DEFAULT_MAX_STEPS)
        max_steps_spin = tk.Spinbox(
            sidebar, from_=1, to=200, textvariable=self.max_steps_var, width=6,
            font=self.font_mono, bg="#44475a", fg=fg,
            buttonbackground="#44475a", relief=tk.FLAT,
            highlightthickness=1, highlightcolor=accent, highlightbackground=comment
        )
        max_steps_spin.pack(anchor=tk.W, pady=2)

        tk.Frame(sidebar, height=1, bg=comment).pack(fill=tk.X, pady=8)

        # Status indicator
        tk.Label(sidebar, text="Status:", font=self.font_label, bg=panel_bg, fg=comment).pack(anchor=tk.W)
        self.status_var = tk.StringVar(value="Idle")
        self.status_label = tk.Label(
            sidebar, textvariable=self.status_var, font=self.font_mono,
            bg=panel_bg, fg=yellow, wraplength=170, justify=tk.LEFT
        )
        self.status_label.pack(anchor=tk.W, pady=2)

        # Step counter
        tk.Label(sidebar, text="Step:", font=self.font_label, bg=panel_bg, fg=comment).pack(anchor=tk.W, pady=(8, 0))
        self.step_var = tk.StringVar(value="0 / —")
        tk.Label(
            sidebar, textvariable=self.step_var, font=self.font_mono,
            bg=panel_bg, fg=fg
        ).pack(anchor=tk.W)

        tk.Frame(sidebar, height=1, bg=comment).pack(fill=tk.X, pady=8)

        # System Prompt toggle
        self.sys_prompt_visible = tk.BooleanVar(value=False)
        self.btn_toggle_prompt = tk.Button(
            sidebar, text="📝  Edit System Prompt", font=self.font_btn,
            bg="#44475a", fg=fg, activebackground="#6272a4", activeforeground=fg,
            relief=tk.FLAT, cursor="hand2", command=self._toggle_system_prompt
        )
        self.btn_toggle_prompt.pack(fill=tk.X, pady=2)

        # Clear chat
        self.btn_clear = tk.Button(
            sidebar, text="🗑  Clear Chat", font=self.font_btn,
            bg="#44475a", fg=comment, activebackground="#6272a4", activeforeground=fg,
            relief=tk.FLAT, cursor="hand2", command=self._on_clear_chat
        )
        self.btn_clear.pack(fill=tk.X, pady=2)

        # ── Right panel (chat + system prompt) ──
        right_panel = tk.Frame(body, bg=bg)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # System prompt editor (hidden by default)
        self.sys_prompt_frame = tk.Frame(right_panel, bg=panel_bg, padx=4, pady=4,
                                          highlightthickness=1, highlightbackground=accent)
        # Don't pack yet — toggled by button

        tk.Label(
            self.sys_prompt_frame, text="System Prompt (editable)",
            font=self.font_label, bg=panel_bg, fg=accent
        ).pack(anchor=tk.W)

        self.sys_prompt_text = scrolledtext.ScrolledText(
            self.sys_prompt_frame, wrap=tk.WORD, height=10,
            font=self.font_mono, bg="#1a1a2e", fg=fg,
            insertbackground=fg, relief=tk.FLAT,
            highlightthickness=1, highlightcolor=accent, highlightbackground=comment
        )
        self.sys_prompt_text.pack(fill=tk.X, pady=4)
        self.sys_prompt_text.insert(tk.END, DEFAULT_SYSTEM_PROMPT)

        # Chat log
        self.chat_log = scrolledtext.ScrolledText(
            right_panel, wrap=tk.WORD, state=tk.DISABLED,
            font=self.font_mono, bg="#11111b", fg=fg,
            insertbackground=fg, relief=tk.FLAT,
            highlightthickness=1, highlightcolor=accent, highlightbackground=comment
        )
        self.chat_log.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        # Configure tags for color coding
        self.chat_log.tag_configure("user", foreground="#89b4fa")        # blue
        self.chat_log.tag_configure("llm", foreground="#a6e3a1")         # green
        self.chat_log.tag_configure("cmd", foreground="#fab387")         # orange
        self.chat_log.tag_configure("sensor", foreground="#6c7086")      # gray
        self.chat_log.tag_configure("error", foreground="#f38ba8")       # red
        self.chat_log.tag_configure("system", foreground="#cba6f7")      # purple
        self.chat_log.tag_configure("thinking", foreground="#a6adc8")    # light gray
        self.chat_log.tag_configure("separator", foreground="#45475a")   # dim

        # ── Input bar ──
        input_frame = tk.Frame(right_panel, bg=panel_bg, padx=6, pady=6,
                               highlightthickness=1, highlightbackground=comment)
        input_frame.pack(fill=tk.X)

        self.input_var = tk.StringVar()
        self.input_entry = tk.Entry(
            input_frame, textvariable=self.input_var,
            font=self.font_input, bg="#1a1a2e", fg=fg,
            insertbackground=fg, relief=tk.FLAT,
            highlightthickness=1, highlightcolor=accent, highlightbackground=comment
        )
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.input_entry.bind("<Return>", lambda e: self._on_send())

        self.btn_send = tk.Button(
            input_frame, text="Send ➤", font=self.font_btn,
            bg=accent, fg="#1e1e2e", activebackground="#9d79d9", activeforeground="#1e1e2e",
            relief=tk.FLAT, cursor="hand2", command=self._on_send, padx=12
        )
        self.btn_send.pack(side=tk.RIGHT)

        # Welcome message
        self._log("system", "━" * 60)
        self._log("system", "  ⚽ LLM Soccer Harness — Ready")
        self._log("system", "  Connect to LM Studio, click 'Start Sim', then type a goal!")
        self._log("system", "━" * 60)
        self._log("system", "")

    # ─────────────────────────────────────────────────────────────────────
    # Chat Log Helpers
    # ─────────────────────────────────────────────────────────────────────
    def _log(self, tag, message):
        """Append a message to the chat log with the given tag (thread-safe)."""
        def _do():
            self.chat_log.configure(state=tk.NORMAL)
            prefix_map = {
                "user": "👤 YOU: ",
                "llm": "🤖 LLM: ",
                "cmd": "⚙️ CMD: ",
                "sensor": "📡 DATA: ",
                "error": "❌ ERR: ",
                "system": "",
                "thinking": "💭 ",
                "separator": "",
            }
            prefix = prefix_map.get(tag, "")
            self.chat_log.insert(tk.END, f"{prefix}{message}\n", tag)
            self.chat_log.see(tk.END)
            self.chat_log.configure(state=tk.DISABLED)
        self.root.after(0, _do)

    def _set_status(self, text):
        """Update the status label (thread-safe)."""
        self.root.after(0, lambda: self.status_var.set(text))

    def _set_step(self, current, maximum):
        """Update the step counter (thread-safe)."""
        self.root.after(0, lambda: self.step_var.set(f"{current} / {maximum}"))

    def _set_controls(self, enabled):
        """Enable/disable input controls (thread-safe)."""
        state = tk.NORMAL if enabled else tk.DISABLED
        def _do():
            self.input_entry.configure(state=state)
            self.btn_send.configure(state=state)
            self.btn_start_sim.configure(state=state)
            self.btn_stop_sim.configure(state=state)
            self.btn_get_data.configure(state=state)
        self.root.after(0, _do)

    # ─────────────────────────────────────────────────────────────────────
    # Button Handlers
    # ─────────────────────────────────────────────────────────────────────
    def _on_start_sim(self):
        """Start the CoppeliaSim simulation."""
        self._log("system", "Starting simulation...")
        threading.Thread(target=self._do_start_sim, daemon=True).start()

    def _do_start_sim(self):
        result = run_agent_command("start_sim")
        if "error" in result:
            self._log("error", f"Start sim failed: {result['error']}")
        else:
            self._log("system", f"✅ {result.get('status', 'Simulation started')}")
        self._set_status("Sim running — Idle")

    def _on_stop_sim(self):
        """Stop the CoppeliaSim simulation."""
        self.stop_event.set()
        self._log("system", "Stopping simulation...")
        threading.Thread(target=self._do_stop_sim, daemon=True).start()

    def _do_stop_sim(self):
        result = run_agent_command("stop_sim")
        if "error" in result:
            self._log("error", f"Stop sim failed: {result['error']}")
        else:
            self._log("system", f"⏹ {result.get('status', 'Simulation stopped')}")
        self._set_status("Sim stopped")

    def _on_get_data(self):
        """Manually fetch sensor data."""
        threading.Thread(target=self._do_get_data_display, daemon=True).start()

    def _do_get_data_display(self):
        result = run_agent_command("get_data")
        self._log("sensor", json.dumps(result, indent=2))

    def _on_emergency_stop(self):
        """Emergency stop: halt loop, stop robot motors."""
        self.stop_event.set()
        self.running_loop = False
        self._log("error", "🛑 EMERGENCY STOP ACTIVATED")
        self._set_status("EMERGENCY STOP")
        self._set_controls(True)
        # Try to stop motors by stopping sim
        threading.Thread(target=lambda: run_agent_command("stop_sim"), daemon=True).start()

    def _on_clear_chat(self):
        """Clear the chat log and reset conversation history."""
        self.chat_log.configure(state=tk.NORMAL)
        self.chat_log.delete("1.0", tk.END)
        self.chat_log.configure(state=tk.DISABLED)
        self.conversation_history = []
        self._set_step(0, "—")
        self._log("system", "Chat cleared. Conversation history reset.")

    def _toggle_system_prompt(self):
        """Show/hide the system prompt editor."""
        if self.sys_prompt_visible.get():
            self.sys_prompt_frame.pack_forget()
            self.sys_prompt_visible.set(False)
            self.btn_toggle_prompt.configure(text="📝  Edit System Prompt")
        else:
            self.sys_prompt_frame.pack(fill=tk.X, pady=(0, 6), before=self.chat_log)
            self.sys_prompt_visible.set(True)
            self.btn_toggle_prompt.configure(text="📝  Hide System Prompt")

    # ─────────────────────────────────────────────────────────────────────
    # Send User Prompt → Start Autonomous Loop
    # ─────────────────────────────────────────────────────────────────────
    def _on_send(self):
        """Handle user sending a prompt."""
        prompt = self.input_var.get().strip()
        if not prompt:
            return
        if self.running_loop:
            self._log("error", "Loop already running. Use Emergency Stop to cancel.")
            return

        self.input_var.set("")
        self._log("user", prompt)
        self._log("separator", "─" * 50)

        # Reset conversation history with current system prompt
        system_prompt = self.sys_prompt_text.get("1.0", tk.END).strip()
        self.conversation_history = [
            {"role": "system", "content": system_prompt}
        ]

        # Start autonomous loop in background thread
        self.stop_event.clear()
        self.running_loop = True
        self._set_controls(False)
        self.loop_thread = threading.Thread(
            target=self._autonomous_loop, args=(prompt,), daemon=True
        )
        self.loop_thread.start()

    # ─────────────────────────────────────────────────────────────────────
    # Autonomous Control Loop
    # ─────────────────────────────────────────────────────────────────────
    def _autonomous_loop(self, user_prompt):
        """Run the LLM → execute → sense → repeat loop."""
        max_steps = self.max_steps_var.get()
        api_url = self.api_url_var.get().strip()

        try:
            # Step 0: Get initial sensor data
            self._set_status("Getting initial sensor data...")
            initial_data = run_agent_command("get_data")
            if "error" in initial_data:
                self._log("error", f"Failed to get initial data: {initial_data['error']}")
                return

            self._log("sensor", f"Initial state: {json.dumps(initial_data)}")

            # Build first user message with sensor context
            first_msg = (
                f"USER GOAL: {user_prompt}\n\n"
                f"CURRENT SENSOR DATA:\n{json.dumps(initial_data, indent=2)}\n\n"
                f"Plan and execute commands to achieve this goal. "
                f"Respond with your first command as JSON."
            )
            self.conversation_history.append({"role": "user", "content": first_msg})

            # ── Main Loop ──
            for step in range(1, max_steps + 1):
                if self.stop_event.is_set():
                    self._log("system", "⏸ Loop stopped by user.")
                    break

                self._set_step(step, max_steps)
                self._set_status(f"Step {step}/{max_steps} — Asking LLM...")

                # 1) Call LLM
                response_text, error = call_llm(api_url, self.conversation_history)

                if error:
                    self._log("error", f"LLM API error: {error}")
                    break

                # 2) Parse response
                parsed = extract_json_from_response(response_text)

                if parsed is None:
                    self._log("error", f"Could not parse LLM response as JSON:")
                    self._log("llm", response_text)
                    # Add to history and ask LLM to fix
                    self.conversation_history.append({"role": "assistant", "content": response_text})
                    self.conversation_history.append({
                        "role": "user",
                        "content": "ERROR: Your response was not valid JSON. Please respond with ONLY a valid JSON object. Example: {\"thinking\": \"...\", \"command\": \"move_forward\", \"distance\": 0.2}"
                    })
                    continue

                # Log thinking if present
                thinking = parsed.get("thinking", "")
                if thinking:
                    self._log("thinking", thinking)

                command_name = parsed.get("command", "").lower().strip()
                self._log("llm", f"Command: {json.dumps(parsed)}")

                # Add assistant response to history
                self.conversation_history.append({"role": "assistant", "content": json.dumps(parsed)})

                # 3) Check for DONE
                if command_name == "done":
                    reason = parsed.get("reason", "Task completed")
                    self._log("system", f"✅ LLM says DONE: {reason}")
                    break

                # 4) Build subprocess args
                sub_args = build_subprocess_args(parsed)
                if sub_args is None:
                    self._log("error", f"Unknown command: {command_name}")
                    self.conversation_history.append({
                        "role": "user",
                        "content": f"ERROR: Unknown command '{command_name}'. Use one of: move_forward, move_backward, rotate, kick, calc_nav, get_data, celebrate, DONE."
                    })
                    continue

                # 5) Execute command
                self._set_status(f"Step {step}/{max_steps} — Executing: {' '.join(sub_args)}")
                self._log("cmd", f"Executing: python llm_soccer_agent.py {' '.join(sub_args)}")

                cmd_result = run_agent_command(*sub_args)
                self._log("cmd", f"Result: {json.dumps(cmd_result)}")

                if "error" in cmd_result:
                    self._log("error", f"Command error: {cmd_result['error']}")

                # Small delay to let physics settle
                time.sleep(0.3)

                # 6) Get fresh sensor data
                self._set_status(f"Step {step}/{max_steps} — Reading sensors...")
                sensor_data = run_agent_command("get_data")
                self._log("sensor", json.dumps(sensor_data))

                # 7) Check for goal scored
                if sensor_data.get("goal_scored", False):
                    self._log("system", "🎉🎉🎉 GOAL SCORED! 🎉🎉🎉")
                    self._set_status("GOAL SCORED! 🎉")
                    # Celebrate
                    run_agent_command("celebrate")
                    self._log("system", "🥳 Celebration complete!")
                    break

                # 8) Feed result + sensor data back to LLM
                feedback = (
                    f"COMMAND RESULT: {json.dumps(cmd_result)}\n\n"
                    f"UPDATED SENSOR DATA:\n{json.dumps(sensor_data, indent=2)}\n\n"
                    f"Continue executing to achieve the goal. Respond with your next command as JSON."
                )
                self.conversation_history.append({"role": "user", "content": feedback})

                # Trim conversation history to prevent context overflow (keep system + last 20 messages)
                if len(self.conversation_history) > 42:
                    self.conversation_history = (
                        self.conversation_history[:1] +  # system prompt
                        self.conversation_history[-20:]   # last 20 messages
                    )

            else:
                # Loop finished without DONE or goal
                self._log("system", f"⚠️ Reached max steps ({max_steps}). Loop stopped.")

        except Exception as e:
            self._log("error", f"Unexpected error in loop: {e}")

        finally:
            self.running_loop = False
            self._set_controls(True)
            self._set_status("Idle")
            self._log("separator", "═" * 50)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    app = SoccerHarnessApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
