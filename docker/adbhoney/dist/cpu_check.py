import psutil
import sys
import time

if len(sys.argv) != 3:
    print("Usage: cpu_check.py <PID> <CPU_USAGE_THRESHOLD>")
    sys.exit(1)

try:
    pid = int(sys.argv[1])
except ValueError:
    print("Please provide a valid integer value for the PID.")
    sys.exit(1)

try:
    cpu_threshold = float(sys.argv[2])
except ValueError:
    print("Please provide a valid number for the CPU usage threshold.")
    sys.exit(1)

try:
    target_process = psutil.Process(pid)
except psutil.NoSuchProcess:
    print(f"No process with the PID {pid} was found.")
    sys.exit(1)

# Prepare to calculate the average CPU usage over 3 intervals of 1 second each
cpu_usages = []
for _ in range(3):
    cpu_usages.append(target_process.cpu_percent(interval=1))

# Calculate the average CPU usage
average_cpu_usage = sum(cpu_usages) / len(cpu_usages)
print(f"Average CPU Usage of PID {pid} over 3 seconds: {average_cpu_usage}%")

# Check average CPU usage against the threshold
if average_cpu_usage >= cpu_threshold:
    print(f"Average CPU usage of PID {pid} is above or equal to the threshold of {cpu_threshold}%.")
    sys.exit(1)
else:
    print(f"Average CPU usage of PID {pid} is below the threshold of {cpu_threshold}%. Exiting with code 0.")
    sys.exit(0)
