import sys
import time     
import math
import json
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

# =============================================================================
# llm_soccer_agent.py — Atomic robot subprocess commands for LLM integration
# =============================================================================
# This program provides single-action commands that a local LLM can call
# via Python's subprocess module to control the soccer simulation.
# =============================================================================

WHEEL_RADIUS = 0.0975
WHEEL_SEPARATION = 0.381
DEFAULT_WHEEL_VEL = 2.0  # rad/s

# Goal box boundaries
GOAL_X_MIN = 2.0
GOAL_X_MAX = 2.5
GOAL_Y_MIN = -1.0
GOAL_Y_MAX = 1.0
GOAL_CENTER_X = (GOAL_X_MIN + GOAL_X_MAX) / 2.0  # 2.25
GOAL_CENTER_Y = (GOAL_Y_MIN + GOAL_Y_MAX) / 2.0  # 0.0

# Strike position offset (robot half-length + ball radius margin + extra safety buffer)
# 0.50 gives ~14cm of clearance from the chassis and allows the kicker to reach the ball's center.
STRIKE_OFFSET = 0.50 

# Kick parameters
KICK_EXTEND_VEL = 4.0
KICK_EXTEND_POS = 0.25
KICK_RETRACT_VEL = 5.0
KICK_RETRACT_POS = 0.0

def print_usage():
    print("Usage:")
    print("  python llm_soccer_agent.py start_sim")
    print("  python llm_soccer_agent.py stop_sim")
    print("  python llm_soccer_agent.py move_forward <meters>")
    print("  python llm_soccer_agent.py move_backward <meters>")
    print("  python llm_soccer_agent.py rotate <degrees>")
    print("  python llm_soccer_agent.py move_pusher <velocity> <position>")
    print("  python llm_soccer_agent.py kick")
    print("  python llm_soccer_agent.py calc_nav <x> <y>")
    print("  python llm_soccer_agent.py get_data")
    print("  python llm_soccer_agent.py celebrate")
    print()
    print("Examples:")
    print("  python llm_soccer_agent.py move_forward 1.5          # Drive forward 1.5 meters")
    print("  python llm_soccer_agent.py rotate 90                 # Turn left 90 degrees")
    print("  python llm_soccer_agent.py kick                      # Atomic kick (push + retract)")
    print("  python llm_soccer_agent.py calc_nav 1.0 -0.5         # Nav data to coordinate")
    print("  python llm_soccer_agent.py move_pusher 4.0 0.25      # Manual pusher control")
    print("  python llm_soccer_agent.py move_pusher 5.0 0.0       # Retract pusher")

if len(sys.argv) < 2:
    print_usage()
    sys.exit(1)

command = sys.argv[1].lower()

try:
    client = RemoteAPIClient()
    sim = client.require('sim')
except Exception as e:
    print(json.dumps({"error": f"Failed to connect to CoppeliaSim: {e}"}))
    sys.exit(1)

def sim_sleep(duration):
    start_time = sim.getSimulationTime()
    while sim.getSimulationTime() - start_time < duration:
        if sim.getSimulationState() == 0:
            break
        time.sleep(0.005)


# --- Helper function to get object handles safely ---
def get_handles():
    try:
        # Try finding the specific kicker from the original scene
        robot = sim.getObject("/PioneerP3DX[0]")
        left_motor = sim.getObject("/PioneerP3DX[0]/leftMotor")
        right_motor = sim.getObject("/PioneerP3DX[0]/rightMotor")
        pusher = sim.getObject("/PioneerP3DX[0]/Prismatic_joint")
    except Exception:
        # Fallback if there's only one robot left in the scene
        try:
            robot = sim.getObject("/PioneerP3DX")
            left_motor = sim.getObject("/PioneerP3DX/leftMotor")
            right_motor = sim.getObject("/PioneerP3DX/rightMotor")
            pusher = sim.getObject("/PioneerP3DX/Prismatic_joint")
        except Exception as e:
            print(json.dumps({"error": f"Could not find robot objects: {e}"}))
            sys.exit(1)
            
    try:
        ball = sim.getObject("/Sphere")
        goal = sim.getObject("/Goalpost")
    except Exception as e:
        print(json.dumps({"error": f"Could not find ball (/Sphere) or goal (/Goalpost): {e}"}))
        sys.exit(1)
        
    return robot, left_motor, right_motor, pusher, ball, goal

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: start_sim
# ─────────────────────────────────────────────────────────────────────────────
if command == "start_sim":
    sim.startSimulation()
    while sim.getSimulationState() == 0:
        time.sleep(0.005)
    try:
        robot, left_motor, right_motor, pusher, ball, goal = get_handles()
        sim.setJointTargetVelocity(left_motor, 0.0)
        sim.setJointTargetVelocity(right_motor, 0.0)
    except Exception:
        pass
    print(json.dumps({"status": "Simulation started and motors stopped"}))

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: stop_sim
# ─────────────────────────────────────────────────────────────────────────────
elif command == "stop_sim":
    sim.stopSimulation()
    print(json.dumps({"status": "Simulation stopped"}))

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: move_forward
# ─────────────────────────────────────────────────────────────────────────────
elif command == "move_forward":
    if len(sys.argv) != 3:
        print(json.dumps({"error": "move_forward requires distance in meters"}))
        sys.exit(1)
    
    try:
        distance = float(sys.argv[2])
    except ValueError:
        print(json.dumps({"error": "Distance must be a number"}))
        sys.exit(1)
        
    robot, left_motor, right_motor, pusher, ball, goal = get_handles()
    
    duration = distance / (DEFAULT_WHEEL_VEL * WHEEL_RADIUS)
    
    sim.setJointTargetVelocity(right_motor, DEFAULT_WHEEL_VEL)
    sim.setJointTargetVelocity(left_motor, DEFAULT_WHEEL_VEL)
    
    sim_sleep(duration)
    
    # Auto-stop
    sim.setJointTargetVelocity(right_motor, 0.0)
    sim.setJointTargetVelocity(left_motor, 0.0)
    
    print(json.dumps({
        "status": "Moved forward",
        "distance_m": distance,
        "duration_s": round(duration, 4)
    }))

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: move_backward
# ─────────────────────────────────────────────────────────────────────────────
elif command == "move_backward":
    if len(sys.argv) != 3:
        print(json.dumps({"error": "move_backward requires distance in meters"}))
        sys.exit(1)
    
    try:
        distance = float(sys.argv[2])
    except ValueError:
        print(json.dumps({"error": "Distance must be a number"}))
        sys.exit(1)
        
    robot, left_motor, right_motor, pusher, ball, goal = get_handles()
    
    duration = distance / (DEFAULT_WHEEL_VEL * WHEEL_RADIUS)
    
    sim.setJointTargetVelocity(right_motor, -DEFAULT_WHEEL_VEL)
    sim.setJointTargetVelocity(left_motor, -DEFAULT_WHEEL_VEL)
    
    sim_sleep(duration)
    
    # Auto-stop
    sim.setJointTargetVelocity(right_motor, 0.0)
    sim.setJointTargetVelocity(left_motor, 0.0)
    
    print(json.dumps({
        "status": "Moved backward",
        "distance_m": distance,
        "duration_s": round(duration, 4)
    }))

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: rotate
# ─────────────────────────────────────────────────────────────────────────────
elif command == "rotate":
    if len(sys.argv) != 3:
        print(json.dumps({"error": "rotate requires angle in degrees"}))
        sys.exit(1)
    
    try:
        angle_deg = float(sys.argv[2])
    except ValueError:
        print(json.dumps({"error": "Angle must be a number"}))
        sys.exit(1)
        
    robot, left_motor, right_motor, pusher, ball, goal = get_handles()
    
    angle_rad = angle_deg * math.pi / 180.0
    robot_omega = (2.0 * DEFAULT_WHEEL_VEL * WHEEL_RADIUS) / WHEEL_SEPARATION
    duration = abs(angle_rad) / robot_omega
    
    if angle_deg > 0:
        # Counter-clockwise (left)
        left_vel = -DEFAULT_WHEEL_VEL
        right_vel = DEFAULT_WHEEL_VEL
    else:
        # Clockwise (right)
        left_vel = DEFAULT_WHEEL_VEL
        right_vel = -DEFAULT_WHEEL_VEL
        
    sim.setJointTargetVelocity(right_motor, right_vel)
    sim.setJointTargetVelocity(left_motor, left_vel)
    
    sim_sleep(duration)
    
    # Auto-stop
    sim.setJointTargetVelocity(right_motor, 0.0)
    sim.setJointTargetVelocity(left_motor, 0.0)
    
    print(json.dumps({
        "status": "Rotated",
        "angle_deg": angle_deg,
        "duration_s": round(duration, 4)
    }))

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: move_pusher
# ─────────────────────────────────────────────────────────────────────────────
elif command == "move_pusher":
    if len(sys.argv) != 4:
        print(json.dumps({"error": "move_pusher requires velocity, position"}))
        sys.exit(1)
        
    try:
        velocity = float(sys.argv[2])
        position = float(sys.argv[3])
    except ValueError:
        print(json.dumps({"error": "Velocity and position must be numbers"}))
        sys.exit(1)
        
    robot, left_motor, right_motor, pusher, ball, goal = get_handles()
    
    sim.setJointTargetVelocity(pusher, velocity)
    sim.setJointTargetPosition(pusher, position)
    
    # Wait a tiny bit to allow the joint to start moving
    time.sleep(0.2)
    
    print(json.dumps({
        "status": "Pusher moved",
        "velocity": velocity,
        "position": position
    }))

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: kick  (atomic push + retract)
# ─────────────────────────────────────────────────────────────────────────────
elif command == "kick":
    robot, left_motor, right_motor, pusher, ball, goal = get_handles()
    
    # Extend pusher
    sim.setJointTargetVelocity(pusher, KICK_EXTEND_VEL)
    sim.setJointTargetPosition(pusher, KICK_EXTEND_POS)
    sim_sleep(0.4)
    
    # Retract pusher
    sim.setJointTargetVelocity(pusher, KICK_RETRACT_VEL)
    sim.setJointTargetPosition(pusher, KICK_RETRACT_POS)
    sim_sleep(0.3)
    
    print(json.dumps({"status": "Kick executed", "pusher_retracted": True}))

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: calc_nav <x> <y>
# ─────────────────────────────────────────────────────────────────────────────
elif command == "calc_nav":
    if len(sys.argv) != 4:
        print(json.dumps({"error": "calc_nav requires x y coordinates"}))
        sys.exit(1)
    
    try:
        tx = float(sys.argv[2])
        ty = float(sys.argv[3])
    except ValueError:
        print(json.dumps({"error": "Coordinates must be numbers"}))
        sys.exit(1)
    
    robot, left_motor, right_motor, pusher, ball, goal = get_handles()
    
    robot_pos = sim.getObjectPosition(robot, -1)
    robot_ori = sim.getObjectOrientation(robot, -1)
    rx, ry, r_yaw = robot_pos[0], robot_pos[1], robot_ori[2]
    
    dist = math.hypot(tx - rx, ty - ry)
    angle_to_target = math.atan2(ty - ry, tx - rx)
    heading_error = angle_to_target - r_yaw
    heading_error = (heading_error + math.pi) % (2 * math.pi) - math.pi
    heading_error_deg = round(heading_error * 180.0 / math.pi, 1)
    
    if abs(heading_error_deg) > 5.0:
        suggestion = f"rotate {heading_error_deg} then move_forward {round(dist, 2)}"
    else:
        suggestion = f"move_forward {round(dist, 2)} (alignment is good)"
    
    print(json.dumps({
        "target": {"x": tx, "y": ty},
        "robot": {"x": round(rx, 4), "y": round(ry, 4)},
        "distance": round(dist, 4),
        "heading_error_deg": heading_error_deg,
        "suggestion": suggestion
    }))

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: get_data
# ─────────────────────────────────────────────────────────────────────────────
elif command == "get_data":
    robot, left_motor, right_motor, pusher, ball, goal = get_handles()
    
    # Get positions
    robot_pos = sim.getObjectPosition(robot, -1)
    robot_ori = sim.getObjectOrientation(robot, -1)
    ball_pos = sim.getObjectPosition(ball, -1)
    goal_pos = sim.getObjectPosition(goal, -1)
    
    rx, ry, r_yaw = robot_pos[0], robot_pos[1], robot_ori[2]
    bx, by = ball_pos[0], ball_pos[1]
    gx, gy = goal_pos[0], goal_pos[1]
    
    # ── Distances ──
    dist_robot_to_ball = math.hypot(bx - rx, by - ry)
    dist_ball_to_goal = math.hypot(GOAL_CENTER_X - bx, GOAL_CENTER_Y - by)
    
    # ── Heading error to ball (degrees) ──
    angle_to_ball = math.atan2(by - ry, bx - rx)
    heading_error_to_ball = angle_to_ball - r_yaw
    heading_error_to_ball = (heading_error_to_ball + math.pi) % (2 * math.pi) - math.pi
    heading_error_to_ball_deg = heading_error_to_ball * 180.0 / math.pi
    
    # ── Kick opportunity ──
    is_ball_in_front = abs(heading_error_to_ball) < 0.2 and dist_robot_to_ball < 0.65
    
    # ── Goal scored detection ──
    # Goal box: x ∈ [2.0, 2.5], y ∈ [-1.0, 1.0]
    goal_scored = (GOAL_X_MIN <= bx <= GOAL_X_MAX) and (GOAL_Y_MIN <= by <= GOAL_Y_MAX)
    
    # ── Ideal strike position (behind the ball, aligned with goal center) ──
    angle_ball_to_goal = math.atan2(GOAL_CENTER_Y - by, GOAL_CENTER_X - bx)
    strike_x = bx - STRIKE_OFFSET * math.cos(angle_ball_to_goal)
    strike_y = by - STRIKE_OFFSET * math.sin(angle_ball_to_goal)
    ideal_yaw = angle_ball_to_goal
    
    # Navigation to strike position
    dist_to_strike = math.hypot(strike_x - rx, strike_y - ry)
    angle_to_strike = math.atan2(strike_y - ry, strike_x - rx)
    heading_error_to_strike = angle_to_strike - r_yaw
    heading_error_to_strike = (heading_error_to_strike + math.pi) % (2 * math.pi) - math.pi
    heading_error_to_strike_deg = heading_error_to_strike * 180.0 / math.pi
    
    # ── Kick alignment check ──
    # Robot is facing the goal, and ball is in front, and distance is right.
    yaw_error_to_ideal = ideal_yaw - r_yaw
    yaw_error_to_ideal = (yaw_error_to_ideal + math.pi) % (2 * math.pi) - math.pi
    
    kick_aligned = (dist_robot_to_ball <= 0.55) and (abs(heading_error_to_ball_deg) <= 7.0) and (abs(yaw_error_to_ideal) < 0.2)
    
    # State machine for suggestion
    if dist_to_strike > 0.2:
        if abs(heading_error_to_strike_deg) > 5.0:
            suggestion = f"rotate {round(heading_error_to_strike_deg, 1)} then move_forward {round(dist_to_strike, 2)} to reach strike position"
        else:
            suggestion = f"move_forward {round(dist_to_strike, 2)} to reach strike position"
    else:
        if abs(heading_error_to_ball_deg) > 5.0:
            suggestion = f"rotate {round(heading_error_to_ball_deg, 1)} to face the ball perfectly"
        elif dist_robot_to_ball > 0.53:
            suggestion = f"move_forward {round(dist_robot_to_ball - 0.50, 2)} to get into kicking range!"
        else:
            suggestion = "You are in position! Use the kick command!"
    
    data = {
        "robot": {"x": round(rx, 4), "y": round(ry, 4)},
        "ball": {"x": round(bx, 4), "y": round(by, 4)},
        "goal_center": {"x": GOAL_CENTER_X, "y": GOAL_CENTER_Y},
        "distances": {
            "robot_to_ball": round(dist_robot_to_ball, 4),
            "ball_to_goal": round(dist_ball_to_goal, 4)
        },
        "heading_error_to_ball_deg": round(heading_error_to_ball_deg, 1),
        "kick_opportunity": is_ball_in_front,
        "strike_position": {
            "x": round(strike_x, 4),
            "y": round(strike_y, 4),
            "heading_error_deg": round(heading_error_to_strike_deg, 1),
            "distance": round(dist_to_strike, 4),
            "suggestion": suggestion
        },
        "kick_aligned": kick_aligned,
        "goal_scored": goal_scored
    }
    
    print(json.dumps(data))

# ─────────────────────────────────────────────────────────────────────────────
# SUBPROCESS: celebrate
# ─────────────────────────────────────────────────────────────────────────────
elif command == "celebrate":
    robot, left_motor, right_motor, pusher, ball, goal = get_handles()
    
    # Spin around
    sim.setJointTargetVelocity(right_motor, 3.0)
    sim.setJointTargetVelocity(left_motor, -3.0)
    
    # Punch out and in repeatedly
    for _ in range(3):
        sim.setJointTargetVelocity(pusher, 5.0)
        sim.setJointTargetPosition(pusher, 0.25)
        time.sleep(0.3)
        sim.setJointTargetPosition(pusher, 0.0)
        time.sleep(0.3)
        
    # Stop wheels
    sim.setJointTargetVelocity(right_motor, 0.0)
    sim.setJointTargetVelocity(left_motor, 0.0)
    
    print(json.dumps({"status": "Celebration complete!"}))

else:
    print(json.dumps({"error": f"Unknown command '{command}'. Available: start_sim, stop_sim, move_forward, move_backward, rotate, move_pusher, kick, calc_nav, get_data, celebrate"}))
    sys.exit(1)
