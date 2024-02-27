import psutil

# Get the overall CPU usage percentage
cpu_usage = psutil.cpu_percent(interval=1)
print(cpu_usage)
# Check CPU usage threshold
if cpu_usage >= 75:  # Adjust the threshold as needed
    exit(1)
else:
    exit(0)
