# T-Pot 🍯 - MacOS🍎 Installation & Testing 📊

<img width="871" alt="image" src="https://github.com/user-attachments/assets/233a2ce6-015c-48d4-96cc-b81347f9970d" />

## Table of Contents
- [Introduction](#introduction)
  - [Features and Benefits](#features-and-benefits)
  - [Architecture](#architecture)
  - [Supported Honeypots](#supported-honeypots)
  - [Tools Included](#tools-included)
- [MacOS Installation](#macos-installation)
  - [Installation Issues](#installation-issues)
  - [Management Tips](#management-tips)
  - [Testing ConPot](#testing-conpot)
  - [Update Script](#update-script)
- [Data Analysis and Insights](#data-analysis-and-insights)
- [Conclusion](#conclusion)

---
<a name="introduction"></a>
## 1. Introduction 🌍
**T-Pot** is an all-in-one honeypot platform designed by Deutsche Telekom. It supports multi-architectures (amd64, arm64) and offers a wide range of visualization options using the **Elastic Stack**, real-time animated attack maps, and numerous security tools to enhance the deception experience. 🍯

---
<a name="features-and-benefits"></a>
### 1.1 Features and Benefits 💡
T-Pot provides several key features that make it a powerful tool for cybersecurity professionals and researchers:

- **Comprehensive Honeypot Integration**: T-Pot combines over 20 honeypots, each designed to capture different types of malicious activity. This integration allows for monitoring and analyzing a wide variety of attack vectors.

- **Elastic Stack Integration**: The platform includes the **ELK stack** (Elasticsearch, Logstash, and Kibana), facilitating data collection, analysis, and visualization. This integration offers powerful tools for real-time threat intelligence.

- **Docker and Docker Compose**: Using Docker and Docker Compose, T-Pot simplifies deployment and management. Each honeypot runs in its own container, ensuring isolation and ease of maintenance.

- **Advanced Visualization Tools**: T-Pot provides tools like **CyberChef**, **Elasticvue**, and a real-time attack map, making it easy to interpret and understand the data collected by the honeypots.

- **Scalability and Flexibility**: T-Pot can be deployed on multiple Linux distributions, macOS, and Windows (with limited functionality). It can run on physical hardware, virtual machines, or cloud environments like AWS.

- **Community Data Sharing**: By default, T-Pot sends data to the **Sicherheitstacho** community backend, contributing to collective threat intelligence. This feature can be disabled if needed.

---
<a name="architecture"></a>
### 1.2 Architecture 🏗️
The core components of T-Pot have been moved into a Docker image called **tpotinit**. This change has made T-Pot compatible with multiple Linux distributions, macOS, and Windows (with some limitations due to Docker Desktop). T-Pot uses **Docker** and **Docker Compose** to run as many honeypots and tools as possible simultaneously, maximizing the host's hardware utilization.

---
<a name="supported-honeypots"></a>
### 1.3 Supported Honeypots 🛡️
T-Pot supports a wide range of honeypots, including:

<a name="industrial-and-medical-honeypots"></a> 
#### 1.3.1 Industrial and Medical Honeypots 🏭
1. **[Conpot](http://conpot.org/)**: Simulates Industrial Control Systems (ICS) and protocols like Modbus, SNMP, and S7comm.
2. **[Dicompot](https://github.com/nsmfoo/dicompot)**: Emulates medical imaging systems (DICOM) to detect attacks on medical devices.
3. **[Medpot](https://github.com/schmalle/medpot)**: Simulates medical data management systems, focusing on healthcare sector attacks.

#### 1.3.2 Network and IoT Honeypots 🌐
1. **[Adbhoney](https://github.com/huuck/ADBHoney)**: Simulates Android devices exposed via the ADB (Android Debug Bridge) protocol.
2. **[Ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot)**: Emulates Cisco ASA devices to detect attacks on firewalls and VPNs.
3. **[Citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot)**: Simulates known Citrix vulnerabilities, such as CVE-2019-19781.
4. **[Dionaea](https://github.com/DinoTools/dionaea)**: Emulates vulnerable network services (e.g., SMB, FTP) to capture malware and exploits.
5. **[Endlessh](https://github.com/skeeto/endlessh)**: Simulates an SSH server that keeps connections open indefinitely, slowing down network scanners.
6. **[Ipphoney](https://gitlab.com/bontchev/ipphoney)**: Emulates IPP (Internet Printing Protocol) services to detect attacks on network printers.

#### 1.3.3 Web and Application Honeypots 🌍
1. **[Cowrie](https://github.com/cowrie/cowrie)**: Emulates SSH and Telnet servers to capture brute-force attempts and malicious commands.
2. **[Hellpot](https://github.com/yunginnanet/HellPot)**: Simulates vulnerable HTTP servers to capture "log4shell" attacks (CVE-2021-44228).

#### 1.3.4 DDoS and Anomaly Detection Honeypots ⚠️
1. **[Ddospot](https://github.com/aelth/ddospot)**: Detects and analyzes DDoS attacks by simulating vulnerable services.
2. **[Honeytrap](https://github.com/armedpot/honeytrap/)**: Monitors network traffic and dynamically launches honeypots based on incoming requests.

#### 1.3.5 Email and Communication Honeypots 📧
1. **[Mailoney](https://github.com/awhitehatter/mailoney)**: Emulates SMTP servers to capture spam and phishing attempts.
2. **[Heralding](https://github.com/johnnykv/heralding)**: Simulates authentication services (e.g., SSH, FTP) to capture stolen credentials.

#### 1.3.6 Malware and Advanced Analysis Honeypots 🦠
1. **[Beelzebub](https://github.com/mariocandela/beelzebub)**: Analyzes malware by emulating vulnerable services.
2. **[Snare](http://mushmush.org/)** / **[Tanner](http://mushmush.org/)**: Snare captures interactions, while Tanner analyzes attacker behavior.

#### 1.3.7 Data Traps and Advanced Deception Honeypots 🎯
1. **[Elasticpot](https://gitlab.com/bontchev/elasticpot)**: Simulates an unprotected Elasticsearch server, often targeted for data breaches.
2. **[H0neytr4p](https://github.com/pbssubhash/h0neytr4p)**: A generic honeypot for capturing interactions with exposed services.

---
<a name="tools-included"></a>
### 1.4 Tools Included 🛠️
T-Pot also includes the following tools:
- **Autoheal**: Automatically restarts containers with failed health checks.
- **CyberChef**: A web app for encryption, encoding, compression, and data analysis.
- **Elastic Stack**: For beautifully visualizing all events captured by T-Pot.
- **Elasticvue**: A web frontend for browsing and interacting with an Elasticsearch cluster.
- **Fatt**: A PyShark-based script for extracting network metadata and fingerprints from PCAP files and live traffic.
- **T-Pot Attack Map**: A beautifully animated attack map for T-Pot.
- **P0f**: A tool for purely passive traffic fingerprinting.
- **Spiderfoot**: An open-source intelligence automation tool.
- **Suricata**: A Network Security Monitoring engine.

---
<a name="macos-installation"></a>
## 2 MacOS Installation 🍏
As Docker Desktop is rather limited not all honeypot types or T-Pot features are supported. Also remember, by default the macOS are blocking access from remote, so testing is limited to the host.

**System Requirements:**
The T-Pot installation needs at least 8-16 GB RAM, 128 GB free disk space as well as a working (outgoing non-filtered) internet connection.

To get things up and running just follow these steps:
1. Install Docker Desktop for [macOS](https://docs.docker.com/desktop/install/mac-install/)
2. Clone the GitHub repository:
   ```sh
   git clone https://github.com/domedg/tpotce_MacOS/
   ```
3. Go to repo folder:
   ```sh
   cd tpotce_MacOS/
   ```
4. Copy the docker configuration file
   ```sh
   cp compose/mac_win.yml ./docker-compose.yml
   ```
5. Check if the script `genuser.sh` is executable, if is not run:
   ```sh
   chmod 777 genuser.sh
   ```
6. Create a `WEB_USER` by running `./genuser.sh` <br>
   If the `WEB_USER` is not properly set, check [Issue 5: WEB_USER Not Loaded](#issue-5-web_user-not-loaded).
    
7. Adjust the `.env` file by changing `TPOT_OSTYPE=linux` to `mac` or directly run:
    ```sh
    sed -i '' 's/^TPOT_OSTYPE=linux$/TPOT_OSTYPE=mac/' .env
    ```
8. You have to ensure on your own there are no port conflicts keeping T-Pot from starting up. Check the [list of required ports](https://github.com/telekom-security/tpotce?tab=readme-ov-file#required-ports).
9. To start T-Pot run:
    ```
    docker compose up
    ```
    or  if you want T-Pot to run in the background:
    ```
    docker compose up -d
    ```
    Before starting T-Pot, make sure Docker is running on your system.
10. During the first time running `docker-compose up`, you may encounter some issues. Check the [Installation Issues](#installation-issues) section to solve them. 
11. To Stop T-Pot press: `CTRL-C` (it if was running in the foreground) and / or `docker compose down -v` to stop T-Pot entirely.

---
<a name="installation-issues"></a>
### 2.1 Installation Issues 🤦‍♂️

In this section, you will find a guide to resolve the installation issues I encountered. Each issue is followed by its respective solution.

#### ⚠️ Issue 1: Undefined Network in Docker Compose
**Issue:** When running `docker compose up`, you receive the error:
```diff
- service "citrixhoneypot" refers to undefined network citrixhoneypot_local: invalid compose project
```
**Solution:** Add the `citrixhoneypot_local` network to the `docker-compose.yml` file:
```yaml
networks:
    citrixhoneypot_local:
```

#### ⚠️ Issue 2: Port Already in Use for Citrixhoneypot
**Issue:** When running `docker compose up`:
```diff
- Citrixhoneypot reports that port 443 is already in use.
```
**Solution:** Change the port from 443 to another free (8445 in this example) in the `docker-compose.yml` file:
```yaml
# CitrixHoneypot service
  citrixhoneypot:
    container_name: citrixhoneypot
    restart: always
    depends_on:   
      tpotinit:
        condition: service_healthy
    networks:
     - citrixhoneypot_local
    ports:
     - "443:8445"
    image: ${TPOT_REPO}/citrixhoneypot:${TPOT_VERSION}
    pull_policy: ${TPOT_PULL_POLICY}  
    read_only: true
    volumes:
     - ${TPOT_DATA_PATH}/citrixhoneypot/log:/opt/citrixhoneypot/logs
```

#### ⚠️ Issue 3: Kibana not working
**Issue:** Kibana service not working
**Solution:** Inside the Kibana container, you need to set the `server.rewriteBasePath=true` variable.

Access the container using the `docker exec` command with the `-u root` option:
```sh
docker exec -u root -it <container_id> /bin/bash
```
To retrieve the container ID, run the following command:
```sh
docker ps -a
```
Edit the `/usr/share/kibana/config/kibana.yml` file by changing this variable from false to true:
```yaml
server.rewriteBasePath: true
```

#### ⚠️ Issue 4: Port Already Mapped for Snare
**Issue:** When running `docker compose up`:Snare reports that port 80 is already mapped.
```diff
- Snare reports that port 80 is already in use.
```
**Solution:** Modify the `docker-compose.yml` file by changing the port mapping from 80 to another available port (5695 for example):
```yaml
## Snare Service   
  snare:
    container_name: snare
    restart: always
    depends_on:
     - tanner  
    tty: true  
    networks:
     - tanner_local
    ports:   
     - "80:5695"
    image: ${TPOT_REPO}/snare:${TPOT_VERSION}
    pull_policy: ${TPOT_PULL_POLICY}
```
<a name="issue-5-web_user-not-loaded"></a>  
#### ⚠️ Issue 5: WEB_USER Not Loaded
**Issue:** Sometimes, running the `genuser.sh` script may not properly load the `WEB_USER` entry in the `.env` file.
**Solution:**
To check, open the `.env` file and verify if the `WEB_USER` variable is empty.  

If it is empty, generate a new value using the following command:  
```sh
htpasswd -n -b "username" "password" | base64 -w0
```
For example, running:
```sh
htpasswd -n -b "tsec" "tsec" | base64 -w0
```
Copy the generated string and manually replace the WEB_USER value in the .env file.

#### ⚠️ Issue 6: Missing Directories in T-Pot Initialization
**Issue:** When running `docker compose up`, you may see the following lines in the T-Pot init logs:
```
tpotinit        | # Updating permissions ...
tpotinit        | 
tpotinit        | chmod: /data/nginx/conf: Permission denied
tpotinit        | chmod: /data/nginx/cert: Permission denied
...
```
**Solution:** To correct this and other issues of the same type, run:
```sh
chmod -R 770 data
```
**Note:** You may also encounter the following errors during T-Pot initialization:
```
tpotinit        | chmod: /data/adbhoney/downloads.tgz: No such file or directory
tpotinit        | chmod: /data/cowrie/log/ttylogs.tgz: No such file or directory
tpotinit        | chmod: /data/cowrie/downloads.tgz: No such file or directory
```
These errors are not critical and T-Pot should still function correctly.

#### ⚠️ Issue 7: ConPot disable by default.
**Issue:** Conpot Not Showing in Kibana Dashboard Due to Missing Network Configuration in `docker-compose.yml`

**Solution:** To fix this issue:

**Step 1** Add the following entry to the `docker-compose.yml` file, in the `networks:` section:
    ```yaml
    conpot_local_default:
    ```
Or, if you want to add all Conpot's template, add:
    ```yaml
    conpot_local_default:
    conpot_local_IEC104:
    conpot_local_guardian_ast:
    conpot_local_ipmi:
    conpot_local_kamstrup_382:
    ```
**Step 2** Add the following entry to the `docker-compose.yml` file, in the `services:` section:
<details>
<summary>Click to expand</summary>

  ```yaml
  # Conpot default service
  conpot_default:
    build: .
    container_name: conpot_default
    restart: always
    environment:
    - CONPOT_CONFIG=/etc/conpot/conpot.cfg
    - CONPOT_JSON_LOG=/var/log/conpot/conpot_default.json
    - CONPOT_LOG=/var/log/conpot/conpot_default.log
    - CONPOT_TEMPLATE=default
    - CONPOT_TMP=/tmp/conpot
    tmpfs:
    - /tmp/conpot:uid=2000,gid=2000
#    cpu_count: 1
#    cpus: 0.25    
    networks:
    - conpot_local_default
    ports:
#    - "69:69/udp"        
    - "80:80"
    - "102:102"
    - "161:161/udp"
    - "502:502"
#     - "623:623/udp"
    - "2121:21"
    - "44818:44818"
    - "47808:47808/udp"
    image: "dtagdevsec/conpot:24.04"
    read_only: false
    volumes:
#     - $HOME/tpotce/data/conpot/log:/var/log/conpot
    - ${TPOT_DATA_PATH}/conpot/log:/var/log/conpot

# Conpot IEC104 service
  conpot_IEC104:
    container_name: conpot_IEC104
    restart: always
    environment:
    - CONPOT_CONFIG=/etc/conpot/conpot.cfg
    - CONPOT_JSON_LOG=/var/log/conpot/conpot_IEC104.json
    - CONPOT_LOG=/var/log/conpot/conpot_IEC104.log
    - CONPOT_TEMPLATE=IEC104
    - CONPOT_TMP=/tmp/conpot
    tmpfs:
    - /tmp/conpot:uid=2000,gid=2000
#    cpu_count: 1
#    cpus: 0.25
    networks:
    - conpot_local_IEC104
    ports:
#     - "161:161/udp"
    - "2404:2404"
    image: "dtagdevsec/conpot:24.04"
    read_only: true
    volumes:
#     - $HOME/tpotce/data/conpot/log:/var/log/conpot
      - ${TPOT_DATA_PATH}/conpot/log:/var/log/conpot

# Conpot guardian_ast service
  conpot_guardian_ast:
    container_name: conpot_guardian_ast
    restart: always
    environment:
    - CONPOT_CONFIG=/etc/conpot/conpot.cfg
    - CONPOT_JSON_LOG=/var/log/conpot/conpot_guardian_ast.json
    - CONPOT_LOG=/var/log/conpot/conpot_guardian_ast.log
    - CONPOT_TEMPLATE=guardian_ast
    - CONPOT_TMP=/tmp/conpot
    tmpfs:
    - /tmp/conpot:uid=2000,gid=2000
#    cpu_count: 1
#    cpus: 0.25
    networks:
    - conpot_local_guardian_ast
    ports:
    - "10001:10001"
    image: "dtagdevsec/conpot:24.04"
    read_only: true
    volumes:
#     - $HOME/tpotce/data/conpot/log:/var/log/conpot
    - ${TPOT_DATA_PATH}/conpot/log:/var/log/conpot

# Conpot ipmi
  conpot_ipmi:
    container_name: conpot_ipmi
    restart: always
    environment:
    - CONPOT_CONFIG=/etc/conpot/conpot.cfg
    - CONPOT_JSON_LOG=/var/log/conpot/conpot_ipmi.json
    - CONPOT_LOG=/var/log/conpot/conpot_ipmi.log
    - CONPOT_TEMPLATE=ipmi
    - CONPOT_TMP=/tmp/conpot
    tmpfs:
    - /tmp/conpot:uid=2000,gid=2000
#    cpu_count: 1
#    cpus: 0.25
    networks:
    - conpot_local_ipmi
    ports:
    - "623:623/udp"
    image: "dtagdevsec/conpot:24.04"
    read_only: true
    volumes:
#     - $HOME/tpotce/data/conpot/log:/var/log/conpot
    - ${TPOT_DATA_PATH}/conpot/log:/var/log/conpot

# Conpot kamstrup_382
  conpot_kamstrup_382:
    container_name: conpot_kamstrup_382
    restart: always
    environment:
    - CONPOT_CONFIG=/etc/conpot/conpot.cfg
    - CONPOT_JSON_LOG=/var/log/conpot/conpot_kamstrup_382.json
    - CONPOT_LOG=/var/log/conpot/conpot_kamstrup_382.log
    - CONPOT_TEMPLATE=kamstrup_382
    - CONPOT_TMP=/tmp/conpot
    tmpfs:
    - /tmp/conpot:uid=2000,gid=2000
#    cpu_count: 1
#    cpus: 0.25
    networks:
    - conpot_local_kamstrup_382
    ports:
    - "1025:1025"
    - "50100:50100"
    image: "dtagdevsec/conpot:24.04"
    read_only: true
    volumes:
#     - $HOME/tpotce/data/conpot/log:/var/log/conpot
    - ${TPOT_DATA_PATH}/conpot/log:/var/log/conpot

  ```
  </details>

---
<a name="management-tips"></a>
### 2.2 Management Tips 🛟

1. **Kibana**: Kibana does not start immediately as it needs to establish a connection with Elasticsearch. Generally, the containers that take the longest to start are Kibana, Dionaea and Conpot. To monitor the status of the containers, you can run:
    ```sh
    docker ps -a 
    ```
    If you want to see containers that have yet to start, you can run:
    ```sh
    docker ps -a | grep starting
    ```

3. **Get Service Ports**: You can get the exposed ports of a container with the following command:
    ```sh
    docker inspect <container_id> | grep "HostPort"
    ```

4. **Get Container ID**: To get the ID of a running container, use:
    ```sh
    docker ps
    ```
    This command lists all currently running containers, showing information such as the container ID, image, command, start time, status, and names.

5. **Get Service Address**: To get the IP address of a container, use:
    ```sh
    docker inspect <container_id> | grep "IPAddress"
    ```

6. **Run a Container with Root Permissions**: To run a shell inside a container with root permissions, use:
    ```sh
    docker exec -it --user root <container_id> /bin/sh
    ```
7. **Prune Unused Networks**: If you encounter network issues, you can remove all unused networks with the following command:
    ```sh
    docker network prune
    ```
    This command will prompt for confirmation before deleting all unused networks.
8. **Restart Containers**: Sometimes, simply restarting the containers can resolve issues. You can do this by bringing the containers down and then up again:
    ```sh
    docker-compose down && docker-compose up
    ```
    This command stops and removes the containers, then recreates and starts them.
   

---
<a name="testing-conpot"></a>
### 2.3 Testing ConPot 🦠

In this section, we will perform tests on the **[Conpot](http://conpot.org/)** honeypot, as mentioned in section [1.3.1 Industrial and Medical Honeypots 🏭](#industrial-and-medical-honeypots) **[Conpot](http://conpot.org/)** simulates Industrial Control Systems (ICS) and protocols like Modbus (port 502), SNMP (port 161), and S7comm (port 102).

**Verify if Conpot exposes the expected services (e.g., port 80 for HTTP, port 502 for Modbus, port 161 for SNMP):**
```sh
nmap -sV -p 1-65535 <indirizzo-IP>

nmap -sS -p- <indirizzo-IP>    # TCP SYN scan (all ports)
nmap -sU -p- <indirizzo-IP>    # UDP scan (all ports)
nmap -sV <indirizzo-IP>        # Service version detection
```
<br>

#### **ModBusSploit 🛠️**

In this section, we will perform tests on the **[ModBusSploit](https://github.com/C4l1b4n/ModBusSploit/)** tool to simulate attacks on the Conpot honeypot with protocols like Modbus.

**Step 1: Clone the ModBusSploit repository:**
```sh
git clone https://github.com/C4l1b4n/ModBusSploit/
cd ModBusSploit
```

**Step 2: Install the required dependencies:**
```sh
pip install -r requirements.txt
```

**Step 3: Run the script**
```sh
python3 start.py
```

**Screenshots:**

1. **Start Dos Attack:**
<img width="537" alt="modbus" src="https://github.com/user-attachments/assets/63b1e25a-a938-4b65-8821-80e0d10b0af9" />

2. **Result on conpot log:**
![conpot_log](https://github.com/user-attachments/assets/e33cb1ab-e89c-4314-b395-9e63147b54b8)

3. **Result on Kibana dashboard:**
<img width="1188" alt="kibana_dash" src="https://github.com/user-attachments/assets/de9c841b-830d-42d9-b778-61c270cc9c8c" />

<br>
<br>
<br>

### **Brute force attack examples using Hydra:**
```sh
hydra -l <utente> -P <file_wordlist> ssh://<indirizzo-IP>
hydra -l <utente> -P <file_wordlist> ftp://<indirizzo-IP>
hydra -l <utente> -P <file_wordlist> http-get://<indirizzo-IP>
```

### **Exploitation example using Metasploit:**
```sh
msfconsole
use exploit/linux/ssh/sshexec
set RHOST <indirizzo-IP>
set USERNAME <utente>
set PASSWORD <password>
exploit
```

### **Example of an XSS attack using curl:**
```sh
curl -X POST -d "username=<script>alert('XSS')</script>" http://<indirizzo-IP>/login
```

### **SQL injection example using sqlmap:**
```sh
sqlmap -u "http://<indirizzo-IP>/page?id=1" --risk=3 --level=5
```

### **Netcat example to connect to port 80 (HTTP):**
```sh
nc -v <indirizzo-IP> 80
```

---
<a name="update-script"></a>
### Update Script 🔄
T-Pot releases are offered through GitHub and can be pulled using 
```
./update.sh
```

***If you made any relevant changes to the T-Pot config files make sure to create a backup first!*** <br>
***Updates may have unforeseen consequences. Create a backup of the machine or the files most valuable to your work!***  

The update script will ...
 - **mercilessly** overwrite local changes to be in sync with the T-Pot master branch
 - create a full backup of the `~/tpotce` folder
 - update all files in `~/tpotce` to be in sync with the T-Pot master branch
 - restore your custom `ews.cfg` from `~/tpotce/data/ews/conf` and the T-Pot configuration (`~/tpotce/.env`).

---

<a name="data-analysis-and-insights"></a>
## 3 Data Analysis and Insights 📊
Recent studies, such as one conducted by **Jiuma Elhshik** ([source](https://medium.com/@jiumael/trap-the-hackers-leveraging-t-pot-for-threat-intelligence-part-2-b7a96848a09b)), have demonstrated T-Pot's effectiveness in collecting and analyzing threat data. Over 48 hours, T-Pot captured **126,833 attacks**, providing valuable insights into current threat landscapes. Key findings include:

1. **Most Targeted Honeypots**:
   - **Dionaea**: Over 47,000 attacks, primarily targeting SMB (port 445).
   - **DDospot**: Specialized in detecting DDoS attacks.
   - **Honeytrap**: Attracted a wide range of attacks.

2. **Geographical Origin of Attacks**:
   - Most attacks originated from the **United States** and **China**, with significant activity from **Iran** and the **Netherlands**. Note that IP spoofing may obscure true origins.

3. **Exploited Vulnerabilities**:
   - **CVE-2023-50387 (KeyTrap)**: Targets DNS servers.
   - **CVE-2023-46604**: A deserialization vulnerability in Apache ActiveMQ.

4. **Attack Techniques**:
   - Brute-force attempts on SSH and Telnet services.
   - Use of backdoors like **DoublePulsar**.
   - Detection of malware such as **Hajime**, a worm known for creating botnets.

For more detailed information, you can read the full study by Jiuma Elhshik on [Medium](https://medium.com/@jiumael/trap-the-hackers-building-and-analyzing-a-t-pot-honeypot-b15f3b6c5ea2).

---
<a name="conclusion"></a>
## 4 Conclusion 🔚
T-Pot is a powerful and versatile platform for cybersecurity professionals and researchers. Its ability to integrate multiple honeypots, provide advanced visualization tools, and scale across different environments makes it an essential tool for understanding and mitigating cyber threats. By contributing to collective threat intelligence, T-Pot helps build a safer digital world. 🌐🔒

---
